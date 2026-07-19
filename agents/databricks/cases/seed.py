"""Deterministic SYNTHETIC seed for `gold.resolved_cases` — the Tier-3 case index.

Why this exists
---------------
`search_similar_cases` answers "have we seen a case like this before?" against a Vector
Search index that DELTA_SYNCs from `gold.resolved_cases`. That table is written by human
analysts resolving real investigations — features, their notes, and the confirmed outcome.
Nothing in this repository can produce it, and nothing ever will: its content IS the
accumulated human judgement, which is precisely what cannot be simulated.

So the copilot shipped with an empty index and Tier 3 could not be demonstrated at all.
This module seeds it, so the loop `resolved_cases -> DELTA_SYNC -> index -> search` is
provably wired end to end.

The honesty problem, and why it is permanent
--------------------------------------------
The synthetic REGULATORY corpus was replaced with the real EUR-Lex text. These cases can
never be replaced — there are no real investigations to substitute. The risk that someone
reads a retrieved case as institutional knowledge is therefore not transitional, it is
forever, and it is quiet: the analyst asks "have we seen this?", gets a confident answer,
and has no way to know it was invented.

Hence every case declares itself, in three places that all survive retrieval:

* `case_id` is prefixed `SYNTH-`
* a `provenance` column carries `synthetic-seed`
* **`case_text` OPENS with the disclosure** — this is the load-bearing one, because
  `case_text` is the column that gets embedded and returned to the analyst. A marker that
  lives only in a metadata column is a marker the human never sees.

`tests/agents/databricks/test_case_seed.py` enforces all three.

Design
------
Cases are derived from the funnel's OWN fraud archetypes (`simulator.generator
.FraudPattern`) and score bands (`ml.serving.scorer`), not invented independently — a case
the system could never produce would surface as a "similar case" that resembles nothing
real. Generation is seeded and deterministic (no `uuid4`, no `now()`) so the fixture is
reproducible and testable.

Outcomes are deliberately MIXED. A corpus where every case is confirmed fraud teaches an
analyst nothing: the value of "have we seen this?" is the ratio — *four were fraud, one
was a traveller*. The per-archetype rates below encode that a country mismatch is often a
holiday and an unusual hour is a night shift, while a 100x amount outlier rarely is.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from simulator.generator import FraudPattern

# The marker that must reach the analyst's screen, not just the metadata.
PROVENANCE = "synthetic-seed"
CASE_ID_PREFIX = "SYNTH-"
DISCLOSURE = (
    "SYNTHETIC SEED CASE — generated fixture for demonstrating the case index. "
    "This is NOT a real investigation and carries no institutional knowledge."
)

# Fixed epoch: `datetime.now()` would make the fixture non-reproducible.
_EPOCH = datetime(2026, 1, 6, tzinfo=UTC)

# P(confirmed fraud | archetype). Calibrated to the archetype's real discriminating power:
# a 100x amount outlier is rarely legitimate; a night-time purchase very often is.
_FRAUD_RATE: dict[FraudPattern, float] = {
    FraudPattern.AMOUNT_OUTLIER: 0.85,
    FraudPattern.VELOCITY_SPIKE: 0.80,
    FraudPattern.COUNTRY_MISMATCH: 0.65,
    FraudPattern.NEW_DEVICE: 0.60,
    FraudPattern.UNUSUAL_HOUR: 0.45,
}

# The TreeSHAP drivers each archetype pushes to the top of `top_features`.
_DRIVERS: dict[FraudPattern, tuple[str, ...]] = {
    FraudPattern.AMOUNT_OUTLIER: ("amount_zscore", "amount_usd", "mcc_risk_tier"),
    FraudPattern.VELOCITY_SPIKE: ("txn_velocity_1h", "amount_sum_1h", "distinct_merchants_24h"),
    FraudPattern.COUNTRY_MISMATCH: ("country_mismatch", "distinct_countries_24h", "amount_zscore"),
    FraudPattern.NEW_DEVICE: ("device_seen_before", "device_txn_count_24h", "card_age_days"),
    FraudPattern.UNUSUAL_HOUR: ("is_unusual_hour", "amount_zscore", "txn_velocity_1h"),
}

# What the analyst wrote. Two per archetype: the one that was fraud, the one that was not.
_FRAUD_NOTE: dict[FraudPattern, str] = {
    FraudPattern.AMOUNT_OUTLIER: (
        "Amount {amount:,.2f} USD against a card whose 30-day mean is {mean:,.2f}. No prior "
        "purchase within an order of magnitude. Cardholder unreachable at the time of the "
        "attempt and later disputed the charge."
    ),
    FraudPattern.VELOCITY_SPIKE: (
        "{velocity} authorisations inside one hour across {merchants} distinct merchants, "
        "against a baseline of one to two per day. Pattern is machine-paced, not human."
    ),
    FraudPattern.COUNTRY_MISMATCH: (
        "Billing country differs from the card's usual. No travel signal on the account: no "
        "airline or hotel spend, no gradual geographic drift — the location changes in a "
        "single step from a domestic purchase {gap} hours earlier."
    ),
    FraudPattern.NEW_DEVICE: (
        "First transaction from this device on a card {age} days old. Device fingerprint "
        "unseen across the whole portfolio, and the first action taken from it was a "
        "high-value purchase rather than a balance check."
    ),
    FraudPattern.UNUSUAL_HOUR: (
        "Purchase at {hour:02d}:00 against a card that has never transacted outside "
        "business hours. Combined with an elevated amount, the timing was not incidental."
    ),
}

_LEGIT_NOTE: dict[FraudPattern, str] = {
    FraudPattern.AMOUNT_OUTLIER: (
        "Amount {amount:,.2f} USD is far above the card's {mean:,.2f} mean, but the "
        "cardholder confirmed a single planned purchase (appliance retailer). Genuine."
    ),
    FraudPattern.VELOCITY_SPIKE: (
        "{velocity} authorisations in one hour resolved to a single merchant retrying a "
        "failed payment terminal, not {merchants} independent purchases. Merchant "
        "integration fault, not fraud."
    ),
    FraudPattern.COUNTRY_MISMATCH: (
        "Country differs from the card's usual because the cardholder was travelling. "
        "Confirmed by the customer, and corroborated by an airline charge {gap} hours "
        "earlier that the window did not span. Genuine."
    ),
    FraudPattern.NEW_DEVICE: (
        "New device on a card {age} days old resolved to the cardholder replacing a lost "
        "handset. Identity confirmed on callback; the device has transacted normally since."
    ),
    FraudPattern.UNUSUAL_HOUR: (
        "Purchase at {hour:02d}:00 is outside the card's usual band, but the cardholder "
        "works nights and this merchant recurs monthly on the account. Genuine."
    ),
}


@dataclass(frozen=True)
class ResolvedCase:
    """One row of `gold.resolved_cases` — the DELTA_SYNC source for the case index."""

    case_id: str
    transaction_id: str
    card_hash: str
    event_time: datetime
    archetype: str
    fraud_score: float
    decision: str
    drivers: str
    summary: str
    disposition: str
    outcome: str
    case_text: str
    provenance: str

    def as_row(self) -> dict[str, Any]:
        return asdict(self)


def _decision(score: float) -> str:
    """Mirror `ml.serving.scorer.decision_hint`'s bands so a seeded case is one the
    funnel could actually have produced."""
    from ml.serving.scorer import ScoringConfig, decision_hint

    return decision_hint(score, ScoringConfig())


def _case_text(
    *,
    case_id: str,
    card_hash: str,
    event_time: datetime,
    score: float,
    decision: str,
    drivers: tuple[str, ...],
    archetype: FraudPattern,
    note: str,
    disposition: str,
    outcome: str,
) -> str:
    """The embedded column. Opens with the disclosure — see the module docstring."""
    return "\n".join(
        [
            DISCLOSURE,
            "",
            f"Case {case_id} · card {card_hash} · {event_time:%Y-%m-%d %H:%M} UTC",
            f"Archetype: {archetype.value}. Tier-1 score {score:.2f} ({decision}).",
            f"Leading drivers: {', '.join(drivers)}.",
            "",
            f"Analyst notes: {note}",
            "",
            f"Disposition: {disposition}. Outcome: {outcome}.",
        ]
    )


def build_seed_cases(*, count: int = 60, seed: int = 42) -> tuple[ResolvedCase, ...]:
    """Deterministic synthetic resolved cases, evenly spread across the archetypes.

    Same `seed` and `count` always produce byte-identical cases: the fixture is a fact
    about the repository, not a fresh roll of the dice on every run.
    """
    if count <= 0:
        raise ValueError("count must be positive")

    rng = random.Random(seed)
    patterns = list(FraudPattern)
    cases: list[ResolvedCase] = []

    for i in range(count):
        archetype = patterns[i % len(patterns)]
        is_fraud = rng.random() < _FRAUD_RATE[archetype]

        # A confirmed fraud sits higher in the score band than a false positive — that is
        # what made it a false POSITIVE rather than a miss.
        score = round(rng.uniform(0.88, 0.99) if is_fraud else rng.uniform(0.70, 0.89), 2)
        decision = _decision(score)

        card_hash = f"c{rng.randrange(16**8):08x}"
        amount = round(rng.uniform(1800, 9500), 2) if is_fraud else round(rng.uniform(900, 4200), 2)
        mean = round(rng.uniform(35, 140), 2)
        velocity = rng.randint(9, 24)
        merchants = rng.randint(4, 11)
        age = rng.randint(2, 90)
        gap = rng.randint(1, 9)
        hour = rng.choice([0, 1, 2, 3, 4, 5])
        event_time = _EPOCH + timedelta(hours=rng.randrange(24 * 120), minutes=rng.randrange(60))

        template = (_FRAUD_NOTE if is_fraud else _LEGIT_NOTE)[archetype]
        note = template.format(
            amount=amount,
            mean=mean,
            velocity=velocity,
            merchants=merchants,
            age=age,
            gap=gap,
            hour=hour,
        )

        if is_fraud:
            outcome = "confirmed_fraud"
            # A confirmed fraud over the AMLD Art. 11 occasional-transaction threshold is
            # reported, not merely blocked.
            disposition = "escalated_sar" if amount >= 5000 else "blocked_confirmed"
        else:
            outcome = "false_positive"
            disposition = "released_after_review"

        case_id = f"{CASE_ID_PREFIX}{i:04d}"
        drivers = _DRIVERS[archetype]
        summary = (
            f"{archetype.value.replace('_', ' ')} · {amount:,.2f} USD · "
            f"score {score:.2f} · {outcome}"
        )

        cases.append(
            ResolvedCase(
                case_id=case_id,
                transaction_id=f"{CASE_ID_PREFIX}TXN-{i:04d}",
                card_hash=card_hash,
                event_time=event_time,
                archetype=archetype.value,
                fraud_score=score,
                decision=decision,
                drivers=",".join(drivers),
                summary=summary,
                disposition=disposition,
                outcome=outcome,
                case_text=_case_text(
                    case_id=case_id,
                    card_hash=card_hash,
                    event_time=event_time,
                    score=score,
                    decision=decision,
                    drivers=drivers,
                    archetype=archetype,
                    note=note,
                    disposition=disposition,
                    outcome=outcome,
                ),
                provenance=PROVENANCE,
            )
        )

    return tuple(cases)


def resolved_cases_schema() -> str:
    """DDL for `gold.resolved_cases`, matching `ResolvedCase` field-for-field.

    `case_text` is the column the Vector Search index embeds; `case_id` is its primary key
    (see `infra/bundles/resources/vector_search.yml`).
    """
    return (
        "case_id string, transaction_id string, card_hash string, event_time timestamp, "
        "archetype string, fraud_score double, decision string, drivers string, "
        "summary string, disposition string, outcome string, case_text string, "
        "provenance string"
    )


def outcome_mix(cases: tuple[ResolvedCase, ...]) -> dict[str, dict[str, int]]:
    """Per-archetype outcome counts — what makes "have we seen this?" informative."""
    mix: dict[str, dict[str, int]] = {}
    for case in cases:
        mix.setdefault(case.archetype, {}).setdefault(case.outcome, 0)
        mix[case.archetype][case.outcome] += 1
    return mix
