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
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from time import perf_counter

from agents.bedrock.eval.decision_log import (
    DecisionLogError,
    DecisionRecord,
    DecisionSink,
    JsonlSink,
    new_decision_id,
    record_decision,
    utc_now,
)
from agents.bedrock.eval.judge import VerdictContext, evaluate_verdict, provision_pairs
from agents.bedrock.guardrails.policy import GuardrailPolicy
from ml.features.adapter_stream import FeatureComputationError, compute_features
from ml.serving.local_model import train_demo_scorer
from ml.serving.metrics import ServingMetrics, serve_metrics
from ml.serving.scorer import DECISION_ALLOW, DECISION_BLOCK, DECISION_REVIEW, FraudScorer

logger = logging.getLogger("fintelliguard.stream")


class _NeverRaisedError(Exception):
    """Only `scripts/gate_proof.py` uses this: its `pii-refusal-crashes-the-funnel` attack
    narrows `_record`'s except clause to this class, proving the funnel really does survive
    a refused audit record rather than surviving because nothing ever refuses one."""


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


# The Tier-2 reasoner seam: scored transaction -> (verdict, context) for the acceptance gate.
# build_stub_verdict is the offline default; a live-Bedrock deployment injects its own.
VerdictBuilder = Callable[[dict], tuple[dict, VerdictContext]]


# Where a withheld verdict goes. A rejection with nowhere to go is a rejection nobody acts
# on; naming the destination is the minimum an "effective human oversight" claim needs.
_REVIEW_QUEUE = "tier3.analyst_review"


def _record(
    decisions: DecisionSink,
    decision_id: str,
    record,
    scored: dict,
    tier2: dict,
    metrics: ServingMetrics,
) -> None:
    """Write the decision record. NEVER lets a logging failure stop the funnel.

    `record_decision` raises `DecisionLogError` when a record would carry raw PII — and
    nothing caught it. So the control's trigger condition was a **payment-scoring outage**:
    an unhashed `card_hash` upstream (exactly the scenario the control exists for) raised
    out of `process_transaction`, out of the consumer loop, closed the consumer with the
    offset uncommitted, and the restart re-read the same message. A crash loop, from a
    guardrail. `FeatureComputationError` eight lines above was carefully quarantined
    per-row; the *security* control was the one that killed the process.

    A refused record is an incident — counted, and the transaction is quarantined — not a
    reason to stop scoring. Same for a sink that throws: a full disk must not take payments
    down.
    """
    # Built OUTSIDE the try. A KeyError or TypeError here — `scored` missing a key, a bad
    # `tier2` — is a PROGRAMMING bug, and it must be loud, not swallowed and miscounted as a
    # compliance refusal. The try wraps only the things that legitimately fail at runtime on
    # a healthy build: the PII refusal (DecisionLogError) and a broken sink (OSError).
    entry = DecisionRecord(
        decision_id=decision_id,
        transaction_id=record.transaction_id,
        card_hash=record.card_hash,
        recorded_at=utc_now(),
        # The field every consumer used to drop. Without it no past decision can be tied to
        # the model that made it.
        model_version=str(scored["model_version"]),
        features=record.features.as_dict(),
        fraud_score=float(scored["fraud_score"]),
        decision_hint=str(scored["decision_hint"]),
        top_features=tuple(f["name"] for f in scored["top_features"]),
        **tier2,
    )
    try:
        record_decision(decisions, entry)
    except (DecisionLogError, OSError):
        # The PII refusal and a failed write have the same correct handling: count it, page
        # someone, keep scoring. An unrecorded decision is a compliance failure and must be
        # visible; it is not a reason to stop scoring the next transaction. A programming
        # error is NOT caught here — it belongs in a test failure, not a metric.
        metrics.record_decision_log_refusal()
        logger.exception("decision %s was not recorded", decision_id)


def estimate_grounding(reasoning: str, references: tuple[str, ...]) -> float:
    """A grounding score for the local funnel: 1.0 if every provision cited was retrieved.

    Binary, deliberately, and that is the fix. It used to be the FRACTION of cited
    provisions the context contained, which a verdict defeats by dilution:

        "Per PSD2 Art. 97, AMLD5 Art. 13, EBA GL 2021/03 and the fabricated Art. 999"
        -> 3 of 4 grounded -> 0.75 -> PASSES a 0.75 threshold

    Name enough real regulation and an invented article rides through on the average. A
    proportion is the wrong measure: one fabricated provision is disqualifying, exactly as
    the verdict gate treats it (set difference, any -> reject). Bedrock's contextual-grounding
    filter returns a continuous score over the whole claim; this is not that, and does not
    pretend to be — it is a deterministic stand-in for the one property that matters offline.

    Before this it was worse: `evaluate_output(..., grounding_score=1.0)`, a literal, so
    `policy.py`'s threshold branch could never fire at all.
    """
    cited = provision_pairs(reasoning)
    if not cited:
        # Reasoning that cites nothing is grounded in nothing.
        return 0.0
    available: set[tuple[str, str]] = set()
    for ref in references:
        available |= provision_pairs(ref)
    return 1.0 if cited <= available else 0.0


def process_transaction(
    contract: dict,
    history: CardHistoryStore,
    scorer: FraudScorer,
    guardrail: GuardrailPolicy,
    metrics: ServingMetrics,
    decisions: DecisionSink | None = None,
    verdict_builder: VerdictBuilder | None = None,
) -> dict:
    """Run one transaction through the whole funnel; update metrics; return a summary.

    `decisions` receives one `DecisionRecord` per scored transaction — EVERY transaction,
    not only the flagged ones. The AI-Act document already claims this exists; nothing
    implemented it, and the nearest thing was a stdout log line for flagged cases carrying
    no model version.

    `verdict_builder` is the Tier-2 reasoner seam. Default is the offline STUB; a deployment
    with a live Bedrock agent injects a builder that returns the SAME `(verdict, context)`
    shape from the agent's structured output, so the real acceptance gate + guardrail below
    judge a live verdict without changing this escalation flow. The escalation itself —
    flagged transaction -> Tier 2 — is unconditional and automatic; only the reasoner varies.
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
    # Tier 2 (verdict gate + guardrail) only for flagged transactions. This is the automatic
    # Tier-1 -> Tier-2 escalation: no human, no prompt — the score alone triggers it.
    if scored["decision_hint"] in (DECISION_REVIEW, DECISION_BLOCK):
        # Resolved by NAME at call time (not captured as a default), so a test or a live
        # deployment that patches build_stub_verdict is honoured.
        build = verdict_builder or build_stub_verdict
        verdict, context = build(scored)
        gate = evaluate_verdict(verdict, context)
        metrics.record_verdict(gate.accepted)
        grounding = estimate_grounding(verdict["reasoning"], STUB_REFERENCES)
        guard = guardrail.evaluate_output(verdict["reasoning"], grounding_score=grounding)
        if guard.blocked:
            metrics.record_guardrail_block(guard.policy)

        # A blocked or rejected verdict is WITHHELD, not merely counted.
        #
        # `if guard.blocked: metrics.record_guardrail_block(...)` was the entire effect: a
        # Prometheus counter went up and the verdict shipped anyway — so a verdict the
        # guardrail had just identified as leaking a card number was returned to the caller
        # and written verbatim into the audit log, with the guardrail's own finding attached
        # as metadata. The control detected the leak and passed it on. `blocked` has to mean
        # blocked.
        released = gate.accepted and not guard.blocked
        result["verdict_accepted"] = gate.accepted
        result["verdict_released"] = released
        if not released:
            # `withheld` is what the caller sees. The verdict itself still reaches the
            # decision record — an audit trail that omits the rejected verdict cannot answer
            # "what did the agent try to say?", which is the question a rejection raises.
            result["withheld_because"] = (
                f"guardrail:{guard.policy}" if guard.blocked else f"gate:{gate.failures[0]}"
            )
            result["review_queue"] = _REVIEW_QUEUE

        tier2 = {
            "verdict": verdict,
            "gate_accepted": gate.accepted,
            "gate_failures": gate.failures,
            "guardrail_blocked": guard.blocked,
            "guardrail_policy": guard.policy,
            "grounding_score": grounding,
            "released": released,
        }

    if decisions is not None:
        _record(decisions, decision_id, record, scored, tier2, metrics)

    history.append(card, contract)
    return result


def _consumer_conf(
    bootstrap_servers: str, security_protocol: str, sasl_mechanism: str, region: str
) -> dict:
    """Base consumer config, plus MSK IAM (SASL_SSL/OAUTHBEARER) when asked for.

    Default PLAINTEXT for local Docker Kafka, so the local funnel is untouched. SASL_SSL +
    OAUTHBEARER authenticates to MSK with a token signed at runtime from the task's IAM role
    (no stored secret) — the consumer mirror of the simulator's producer path.
    """
    conf = {
        "bootstrap.servers": bootstrap_servers,
        "group.id": "fintelliguard-scorer",
        "auto.offset.reset": "earliest",
    }
    if security_protocol.upper() == "SASL_SSL" and sasl_mechanism.upper() == "OAUTHBEARER":
        conf["security.protocol"] = "SASL_SSL"
        conf["sasl.mechanisms"] = "OAUTHBEARER"

        def _oauth_cb(_config: str):
            from aws_msk_iam_sasl_signer import MSKAuthTokenProvider

            token, expiry_ms = MSKAuthTokenProvider.generate_auth_token(region)
            return token, expiry_ms / 1000.0

        conf["oauth_cb"] = _oauth_cb
    return conf


def run(
    *,
    bootstrap_servers: str,
    topic: str,
    metrics_port: int,
    scorer: FraudScorer | None = None,
    environment: str = "local",
    max_messages: int | None = None,
    decision_log_path: str = "/var/log/fintelliguard/decisions.jsonl",
    security_protocol: str = "PLAINTEXT",
    sasl_mechanism: str = "",
    region: str = "eu-central-1",
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
        _consumer_conf(bootstrap_servers, security_protocol, sasl_mechanism, region)
    )
    # MSK IAM: poll until the OAUTHBEARER token is served and the brokers are reachable before
    # subscribing. Without this the first poll races ahead of the IAM token and the connection
    # fails with _TRANSPORT (the same fix as the producer and the probe). No-op for PLAINTEXT.
    if security_protocol.upper() == "SASL_SSL":
        import time as _time

        _deadline = _time.time() + 30
        while _time.time() < _deadline and not consumer.list_topics(timeout=5).brokers:
            consumer.poll(0.5)
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
    import os

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
        # MSK IAM is opt-in via env (set on the in-VPC Fargate consumer); default PLAINTEXT.
        security_protocol=os.environ.get("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT"),
        sasl_mechanism=os.environ.get("KAFKA_SASL_MECHANISM", ""),
        region=os.environ.get("AWS_REGION", "eu-central-1"),
    )


if __name__ == "__main__":
    main()
