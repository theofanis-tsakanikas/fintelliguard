"""The decision record — what the AI-Act document already claims exists.

`docs/governance/AI_ACT_ANNEX_IV.md` states, to a regulator:

    every inference is logged (input -> features -> model -> guardrails -> output) for audit

Nothing implemented it. The only thing resembling a log was a `logger.info` in the local
funnel, for flagged cases only, to stdout, carrying no verdict, no gate result, no
correlation id, no model version, and no retention. Record-keeping (AI Act Art. 12) is not
optional for a high-risk system, and it was the largest gap between what the docs promised
and what the code did.

Three properties this module exists to guarantee, each of which was absent:

**The model version travels with the decision.** The scorer's contract carries
`model_version` (`docs/bedrock-integration.md`) and every consumer dropped it on the floor.
"Which model decided this transaction, and what did its card say?" was unanswerable for
every decision the system had ever made — so the generated model card documented a model
that could not be tied to any of its own outputs.

**The record is one row per decision, not a log line.** A decision that cannot be replayed
from its record is not a record. Features, score, drivers, verdict, gate result and
guardrail outcome are fields, not prose.

**PII never enters it.** An audit log of a fraud system is a high-value target. The card
identifier is already hashed upstream; this module refuses to write a record that contains
a raw PAN or email rather than trusting that it does not.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from agents.bedrock.pii import found_pii

# A record that would carry raw PII is refused, not redacted: silently rewriting audit
# evidence is worse than refusing to write it. The patterns come from `agents.bedrock.pii`
# — the guardrail, the verdict gate and this module used to each carry their own copy.


class DecisionLogError(RuntimeError):
    """A decision could not be recorded. Never swallowed: an unrecorded decision is a
    compliance failure, not a logging inconvenience."""


@dataclass(frozen=True)
class DecisionRecord:
    """One transaction's full decision path, as a replayable row.

    Ordered input -> features -> model -> guardrails -> output, matching the sequence the
    AI-Act document describes, so the record can be read against the claim.
    """

    # identity
    decision_id: str
    transaction_id: str
    card_hash: str  # already hashed upstream; never a PAN
    recorded_at: str

    # model — the field whose absence made every past decision unattributable
    model_version: str
    features: Mapping[str, Any]
    fraud_score: float
    decision_hint: str
    top_features: tuple[str, ...]

    # tier 2 — absent when the transaction was never escalated
    verdict: Mapping[str, Any] | None = None
    gate_accepted: bool | None = None
    gate_failures: tuple[str, ...] = field(default_factory=tuple)
    guardrail_blocked: bool | None = None
    guardrail_policy: str | None = None
    grounding_score: float | None = None

    # the policy snapshot in force when this decision was made. Bound to an immutable
    # guardrail version (see agents/bedrock/terraform/guardrail.tf) precisely so this
    # question has an answer.
    guardrail_version: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, default=str)


class DecisionSink(Protocol):
    """Where records go. Injected so the retention/immutability policy is a deployment
    decision (Delta table, S3 with Object Lock, CloudWatch) and not baked in here."""

    def write(self, record: DecisionRecord) -> None: ...


class JsonlSink:
    """Append-only JSONL. The local-funnel sink; the shape a real sink must honour.

    Append-only is the point: a decision record that can be updated in place is not
    evidence. In AWS this is S3 with Object Lock or a Delta table with an audit grant; the
    file here has the same contract and none of the guarantees, which is stated rather than
    implied.
    """

    def __init__(self, path: Path):
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: DecisionRecord) -> None:
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(record.to_json() + "\n")


class MemorySink:
    """Collects records in memory — for tests and for the funnel's default no-op run."""

    def __init__(self) -> None:
        self.records: list[DecisionRecord] = []

    def write(self, record: DecisionRecord) -> None:
        self.records.append(record)


def _pii_in(record: DecisionRecord) -> list[str]:
    # Scan the rendered record, not selected fields: PII arrives in whichever field the
    # author did not think of, which is the whole reason to check the whole thing.
    return found_pii(record.to_json())


def record_decision(sink: DecisionSink, record: DecisionRecord) -> DecisionRecord:
    """Write one decision record, refusing to write raw PII into the audit trail."""
    if hits := _pii_in(record):
        raise DecisionLogError(
            f"decision {record.decision_id} would write raw PII into the audit log "
            f"({hits}); refusing. The card identifier must be hashed upstream and no "
            "verdict text may carry a PAN or an email."
        )
    sink.write(record)
    return record


def new_decision_id() -> str:
    """A correlation id. The one thing that ties a Tier-1 score to its Tier-2 verdict, its
    guardrail outcome and the analyst who eventually opens the case."""
    return str(uuid.uuid4())


def utc_now() -> str:
    return datetime.now(UTC).isoformat()
