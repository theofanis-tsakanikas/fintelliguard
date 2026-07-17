"""Local end-to-end fraud-scoring funnel — the glue the excellent-but-unwired components lacked.

Consumes simulator transactions from Kafka, computes the canonical features with the SAME
`adapter_stream` the Gold layer uses, scores them with the real `FraudScorer` (Tier 1,
TreeSHAP included), runs FLAGGED cases through the REAL Tier-2 verdict-acceptance gate
(`agents.bedrock.eval.judge`) + output guardrail (`agents.bedrock.guardrails.policy`), and
emits Prometheus metrics for Grafana. One process, no Spark, no cloud.

Honesty: the Tier-2 **reasoner** is stubbed here (`build_stub_verdict`) — the live Bedrock
Claude call is deferred to AWS. Everything that *judges* that verdict (the 5-check gate, the
guardrail) is the shipping code, so the funnel and the Responsible-AI gates run end to end.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import deque
from datetime import datetime
from pathlib import Path
from time import perf_counter

from agents.bedrock.eval.decision_log import (
    DecisionRecord,
    DecisionSink,
    JsonlSink,
    new_decision_id,
    record_decision,
    utc_now,
)
from agents.bedrock.eval.judge import VerdictContext, evaluate_verdict, provisions
from agents.bedrock.guardrails.policy import GuardrailPolicy
from ml.features.adapter_stream import FeatureComputationError, compute_features
from ml.serving.local_model import train_demo_scorer
from ml.serving.metrics import ServingMetrics, serve_metrics
from ml.serving.scorer import DECISION_ALLOW, DECISION_BLOCK, DECISION_REVIEW, FraudScorer

logger = logging.getLogger("fintelliguard.stream")

DEFAULT_TOPIC = "txn.raw"
_HISTORY_CAP = 512  # per-card ring buffer; bounds memory over a long run

# Canned regulatory context the STUB reasoner cites. The live Bedrock reasoner would
# retrieve these from the Knowledge Base; fixing them lets the REAL gate + guardrail run
# offline (grounding checks that citations appear in this retrieved context).
STUB_REFERENCES = (
    "PSD2 Art. 97 (Strong Customer Authentication)",
    "AMLD5 Art. 13 (Customer Due Diligence)",
    "EBA GL 2021/03 (fraud reporting)",
)


class CardHistoryStore:
    """Per-card bounded history for the streaming feature adapter.

    The ring buffer bounds how many events a card keeps; it does NOT bound how many cards
    exist, so `first_seen` is tracked separately as a single timestamp per card. That is
    also the fix for `card_age_days`: derived from the buffer, a card's "oldest" event is
    recent by construction once the buffer wraps, so an old busy card could never look old.
    """

    def __init__(self, cap: int = _HISTORY_CAP) -> None:
        self._cap = cap
        self._by_card: dict[str, deque] = {}
        self._first_seen: dict[str, datetime] = {}

    def get(self, card: str) -> list[dict]:
        return list(self._by_card.get(card, ()))

    def first_seen(self, card: str) -> datetime | None:
        """The card's earliest observed transaction — survives the ring buffer wrapping."""
        return self._first_seen.get(card)

    def append(self, card: str, contract: dict) -> None:
        buf = self._by_card.get(card)
        if buf is None:
            buf = deque(maxlen=self._cap)
            self._by_card[card] = buf
        buf.append(contract)
        self._record_first_seen(card, contract)

    def _record_first_seen(self, card: str, contract: dict) -> None:
        try:
            when = datetime.fromisoformat(str(contract.get("timestamp")))
        except (TypeError, ValueError):
            return
        current = self._first_seen.get(card)
        if current is None or when < current:
            self._first_seen[card] = when


def build_stub_verdict(
    scored: dict, references: tuple[str, ...] = STUB_REFERENCES
) -> tuple[dict, VerdictContext]:
    """A well-formed STUB Tier-2 verdict derived from the scorer output.

    The reasoner is stubbed; the gate that judges this verdict is real. Grounded (cites a
    retrieved reference), faithful (leans only on the model's actual top driver), and
    decision-consistent with the score's hint — so it exercises the acceptance path.
    """
    top = tuple(f["name"] for f in scored["top_features"])
    hint = scored["decision_hint"]
    driver = top[0] if top else "the aggregated risk features"
    reasoning = (
        f"The fraud model flagged this transaction; its strongest driver was {driver}. "
        f"The score is consistent with a {hint} recommendation under {references[0]}."
    )
    verdict = {
        "fraud_score": scored["fraud_score"],
        "reasoning": reasoning,
        "drivers": [driver] if top else [],
        "regulatory_reference": references[0],
        "recommended_action": hint,
    }
    context = VerdictContext(top_features=top, retrieved_references=references, decision_hint=hint)
    return verdict, context


def estimate_grounding(reasoning: str, references: tuple[str, ...]) -> float:
    """A crude grounding score for the local funnel: how much of the claim is supported.

    `evaluate_output` was called with `grounding_score=1.0`, a hardcoded constant, so
    `policy.py`'s `grounding_score < grounding_threshold` branch could NEVER fire. The
    GROUNDING policy class was dead code in the only path that runs it, while the local
    README advertised the guardrail as real.

    This is deliberately simple and deliberately not 1.0: it is the fraction of the
    verdict's cited provisions that the retrieved context actually contains. In AWS this
    number comes from Bedrock's contextual-grounding filter; here it at least varies with
    the input, so the threshold is a live control rather than an unreachable branch.
    """
    cited = provisions(reasoning)
    if not cited:
        # Reasoning that cites nothing is not grounded in anything.
        return 0.0
    available: set[str] = set()
    for ref in references:
        available |= provisions(ref)
    return len(cited & available) / len(cited)


def process_transaction(
    contract: dict,
    history: CardHistoryStore,
    scorer: FraudScorer,
    guardrail: GuardrailPolicy,
    metrics: ServingMetrics,
    decisions: DecisionSink | None = None,
) -> dict:
    """Run one transaction through the whole funnel; update metrics; return a summary.

    `decisions` receives one `DecisionRecord` per scored transaction — EVERY transaction,
    not only the flagged ones. The AI-Act document already claims this exists; nothing
    implemented it, and the nearest thing was a stdout log line for flagged cases carrying
    no model version.
    """
    card = str(contract.get("card_hash", ""))
    try:
        record = compute_features(
            contract, history.get(card), card_first_seen=history.first_seen(card)
        )
    except FeatureComputationError:
        metrics.record_quarantine()
        return {"status": "quarantined", "transaction_id": contract.get("transaction_id")}

    started = perf_counter()
    scored = scorer.score(record.features.as_dict())
    metrics.observe_request(perf_counter() - started, scored["decision_hint"])
    metrics.observe_score(scored["fraud_score"])

    decision_id = new_decision_id()
    result = {
        "status": "scored",
        "decision_id": decision_id,
        "transaction_id": record.transaction_id,
        "fraud_score": round(float(scored["fraud_score"]), 4),
        "decision": scored["decision_hint"],
    }

    tier2: dict[str, object] = {}
    # Tier 2 (verdict gate + guardrail) only for flagged transactions.
    if scored["decision_hint"] in (DECISION_REVIEW, DECISION_BLOCK):
        verdict, context = build_stub_verdict(scored)
        gate = evaluate_verdict(verdict, context)
        metrics.record_verdict(gate.accepted)
        grounding = estimate_grounding(verdict["reasoning"], STUB_REFERENCES)
        guard = guardrail.evaluate_output(verdict["reasoning"], grounding_score=grounding)
        if guard.blocked:
            metrics.record_guardrail_block(guard.policy)
        result["verdict_accepted"] = gate.accepted
        tier2 = {
            "verdict": verdict,
            "gate_accepted": gate.accepted,
            "gate_failures": gate.failures,
            "guardrail_blocked": guard.blocked,
            "guardrail_policy": guard.policy,
            "grounding_score": grounding,
        }

    if decisions is not None:
        record_decision(
            decisions,
            DecisionRecord(
                decision_id=decision_id,
                transaction_id=record.transaction_id,
                card_hash=record.card_hash,
                recorded_at=utc_now(),
                # The field every consumer used to drop. Without it no past decision can be
                # tied to the model that made it.
                model_version=str(scored["model_version"]),
                features=record.features.as_dict(),
                fraud_score=float(scored["fraud_score"]),
                decision_hint=str(scored["decision_hint"]),
                top_features=tuple(f["name"] for f in scored["top_features"]),
                **tier2,  # type: ignore[arg-type]
            ),
        )

    history.append(card, contract)
    return result


def run(
    *,
    bootstrap_servers: str,
    topic: str,
    metrics_port: int,
    scorer: FraudScorer | None = None,
    environment: str = "local",
    max_messages: int | None = None,
    decision_log_path: str = "/var/log/fintelliguard/decisions.jsonl",
) -> None:
    """Consume the topic and serve `/metrics`. `confluent_kafka` is imported lazily so the
    module (and its unit tests) stay importable without the native Kafka client."""
    from confluent_kafka import Consumer

    demo = train_demo_scorer()
    scorer = scorer or demo.scorer
    logger.info("demo model held-out AUC: %.3f", demo.holdout_auc)

    metrics = ServingMetrics(environment=environment, model_version=scorer.config.model_version)
    guardrail = GuardrailPolicy()
    history = CardHistoryStore()
    # Append-only decision records — the audit trail the AI-Act document claims. A file
    # here; S3 Object Lock or a Delta table with an audit grant in AWS.
    decisions = JsonlSink(Path(decision_log_path))
    serve_metrics(metrics_port)

    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap_servers,
            "group.id": "fintelliguard-scorer",
            "auto.offset.reset": "earliest",
        }
    )
    consumer.subscribe([topic])
    logger.info(
        "scorer consuming %r from %s; /metrics on :%d", topic, bootstrap_servers, metrics_port
    )

    processed = 0
    try:
        while max_messages is None or processed < max_messages:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                logger.warning("kafka error: %s", msg.error())
                continue
            try:
                contract = json.loads(msg.value().decode("utf-8"))
            except (ValueError, AttributeError):
                metrics.record_quarantine()
                continue
            result = process_transaction(contract, history, scorer, guardrail, metrics, decisions)
            processed += 1
            if result["status"] == "scored" and result["decision"] != DECISION_ALLOW:
                logger.info(
                    "flagged %s score=%.3f -> %s",
                    result["transaction_id"],
                    result["fraud_score"],
                    result["decision"],
                )
    finally:
        consumer.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Local fraud-scoring funnel (Kafka -> features -> scorer -> gate -> metrics)."
    )
    parser.add_argument("--bootstrap-servers", default="localhost:9092")
    parser.add_argument("--topic", default=DEFAULT_TOPIC)
    parser.add_argument("--metrics-port", type=int, default=8000)
    parser.add_argument("--environment", default="local")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run(
        bootstrap_servers=args.bootstrap_servers,
        topic=args.topic,
        metrics_port=args.metrics_port,
        environment=args.environment,
    )


if __name__ == "__main__":
    main()
