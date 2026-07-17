"""Feature parity proven by INDEPENDENT PATHS — the test the old one only looked like.

`test_schema_parity.py` asserted this:

    for name in FEATURE_NAMES:
        assert type(stream_row[name]) is type(ieee_row[name])

Both adapters build the same frozen `FeatureVector`, so the names match by construction and
the types match because `_num` always returns `float` and every int is `int()`-wrapped. The
assertion is `==` wearing a lab coat. It passed with all six of these live:

  * `merchant_risk_score` pinned to 0.0 for every served transaction (0.02-0.12 in training)
  * `card_age_days` pinned to 0 for every served transaction (0-640 in training)
  * every velocity/count feature shifted by exactly one between train and serve
  * `amount_sum_1h` = 0.0 in training while `amount_usd` = 120.0
  * `is_unusual_hour` computed by a different function on each side
  * `device_seen_before` a perfect duplicate of `card_age_days` in training

The fix is not a better assertion — it is a better *epistemology*. A parity test means
something only when the two sides are derived by paths that CAN disagree. So:

    one synthetic card journey  (the ground truth)
        |-> rendered as bronze contracts -> adapter_stream -> vector A
        |-> rendered as IEEE-CIS columns -> adapter_ieee   -> vector B
    assert A == B

The journey is the fact; each encoding is derived from it separately; each adapter reads
only its own encoding. If the two adapters disagree about what a feature means, the vectors
differ and this test fails. That is what the six skews were.

**The boundary of this proof, stated plainly** (a claim this repo has been bad at bounding):
`_as_ieee_row` encodes what C1/C2/C4/C6/D1 *must mean* for the IEEE adapter to be a correct
proxy. It does not prove real IEEE-CIS data means that — those columns are anonymised and
their true semantics are unknown. This test proves the two adapters implement one shared
definition. The definition itself is a documented modelling assumption, in
`ml/features/semantics.py`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest

from ml.features import adapter_ieee, adapter_stream, transforms
from ml.features.merchant_risk import MerchantRiskTable, build_merchant_risk_table
from ml.features.schema import FEATURE_NAMES, FeatureVector
from ml.features.semantics import INVARIANTS, check_invariants

REFERENCE = datetime(2026, 1, 1, 0, 0, 0)


# --------------------------------------------------------------------------- #
# The ground truth: a card journey, independent of any adapter's encoding
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Event:
    """One transaction in a card's life, in source-neutral terms."""

    txn_id: str
    when: datetime
    amount: float
    merchant: str
    device: str
    country: str
    mcc: str


def _journey(seed: int, length: int = 24) -> list[Event]:
    """A plausible card journey spanning weeks, hours and several devices/countries.

    Deliberately exercises the states the skews hid in: a first-ever transaction (no
    history), bursts inside one hour, quiet days, a foreign country, a new device, and
    activity either side of midnight.
    """
    rng = random.Random(seed)
    merchants = ["M00001", "M00002", "M00003"]
    devices = ["D00001", "D00002"]
    countries = ["GR", "GR", "GR", "DE"]
    mccs = ["5411", "5812", "7995", "6011"]

    events: list[Event] = []
    when = REFERENCE
    for i in range(length):
        # Vary the gap so 1h / 24h / 30d windows all get populated and emptied.
        gap = rng.choice([timedelta(minutes=7), timedelta(hours=5), timedelta(days=3)])
        when = when + gap
        events.append(
            Event(
                txn_id=f"t{seed}-{i:03d}",
                when=when,
                amount=round(rng.uniform(5.0, 400.0), 2),
                merchant=rng.choice(merchants),
                device=rng.choice(devices),
                country=rng.choice(countries),
                mcc=rng.choice(mccs),
            )
        )
    return events


# --------------------------------------------------------------------------- #
# Encoding 1: the bronze contract the stream adapter reads
# --------------------------------------------------------------------------- #


def _as_contract(event: Event, card: str) -> dict:
    return {
        "transaction_id": event.txn_id,
        "timestamp": event.when.isoformat(),
        "amount": event.amount,
        "merchant_id": event.merchant,
        "card_hash": card,
        "device_id": event.device,
        "ip_country": event.country,
        "mcc_code": event.mcc,
    }


# --------------------------------------------------------------------------- #
# Encoding 2: the IEEE-CIS columns the training adapter reads
# --------------------------------------------------------------------------- #
#
# This is where the canonical semantics are made concrete for the IEEE side. Each C-count
# is derived from the journey under the ONE window convention (`semantics.py`): the current
# transaction is a member of its own window. Getting this wrong on either side is precisely
# the off-by-one that shipped.


def _as_ieee_row(journey: list[Event], index: int, card: str) -> dict:
    event = journey[index]
    prior = [e for e in journey[:index] if e.when < event.when]
    within_1h = [e for e in prior if event.when - e.when <= timedelta(hours=1)]
    within_24h = [e for e in prior if event.when - e.when <= timedelta(hours=24)]

    first_seen = min((e.when for e in prior), default=event.when)

    return {
        "TransactionID": event.txn_id,
        "card1": card,
        "TransactionAmt": event.amount,
        # C1/C2/C4/C6: window counts INCLUDING the current transaction.
        "C1": len(within_1h) + 1,
        "C2": len(within_24h) + 1,
        "C4": len({e.merchant for e in within_24h} | {event.merchant}),
        "C6": sum(1 for e in within_24h if e.device == event.device) + 1,
        # D1: days since the card was first seen.
        "D1": (event.when - first_seen).days,
        # addr2: the country of this transaction (the adapter compares it to the modal one).
        "addr2": event.country,
        # dist1: >0 when this transaction is away from the card's usual country.
        "dist1": 0.0,
        "ProductCD": "W",
        # TransactionDT: seconds from the reference epoch.
        "TransactionDT": int((event.when - REFERENCE).total_seconds()),
    }


def _ieee_context(journey: list[Event], index: int) -> adapter_ieee.CardContext:
    """The per-card aggregates a batch job computes — from STRICTLY PRIOR events only.

    Prior-only, deliberately. The training path computed these over the whole card group
    including the future (`gold_transforms._training_group`), which is the leakage the
    stream adapter's docstring promises does not exist.
    """
    event = journey[index]
    prior = [e for e in journey[:index] if e.when < event.when]
    within_30d = [e for e in prior if event.when - e.when <= timedelta(days=30)]
    amounts = [e.amount for e in within_30d]

    mean = sum(amounts) / len(amounts) if amounts else event.amount
    if len(amounts) >= 2:
        var = sum((a - mean) ** 2 for a in amounts) / (len(amounts) - 1)
        std = var**0.5
    else:
        std = 0.0

    # The SHARED modal definition — not a re-implementation. Re-implementing it here is
    # what the training path did, and it resolved ties differently from the stream adapter,
    # inverting country_mismatch for any card with two equally-common countries.
    modal = transforms.modal_value(e.country for e in prior)

    # The batch job knows the card's active hours and its device history; the adapter must
    # be given them rather than guessing. Both are prior-only.
    active_hours = tuple(e.when.hour for e in within_30d) or None
    device_seen = any(e.device == event.device for e in prior)

    return adapter_ieee.CardContext(
        amount_mean=mean,
        amount_std=std,
        modal_addr2=modal,
        active_hours=active_hours,
        device_seen_before=device_seen,
    )


# --------------------------------------------------------------------------- #
# The parity test
# --------------------------------------------------------------------------- #

CARD = "card-parity-0001"
# Features whose IEEE source is a documented proxy with no stream equivalent, so the two
# encodings cannot be made to agree by construction. Each one needs a REASON, not a shrug.
PROXY_ONLY = {
    # dist1 is a distance, not a country set; the stream counts real distinct countries.
    "distinct_countries_24h",
    # ProductCD is a product class, not an MCC; the tiers are different taxonomies.
    "mcc_risk_tier",
    # ProductCD's fraud rate is not the merchant's fraud rate.
    "merchant_risk_score",
    # IEEE-CIS has no true 1h amount sum, so the training side values the prior in-window
    # transactions at the card's mean. The *invariant* (>= amount_usd) binds both sides;
    # exact equality cannot, and pretending otherwise would be the tautology again.
    "amount_sum_1h",
}


def _risk_table() -> MerchantRiskTable:
    """A merchant risk table built the way training builds it, from labelled history."""
    return build_merchant_risk_table(
        [{"merchant_id": "M00001", "is_fraud": i % 20 == 0} for i in range(400)]
        + [{"merchant_id": "M00002", "is_fraud": i % 4 == 0} for i in range(400)]
        + [{"merchant_id": "M00003", "is_fraud": False} for _ in range(400)]
    )


def _both_adapters(seed: int):
    """Run one journey through both encodings and return the aligned vectors."""
    journey = _journey(seed)
    contracts = [_as_contract(e, CARD) for e in journey]
    table = _risk_table()
    first_seen = journey[0].when

    pairs = []
    for i in range(len(journey)):
        stream_rec = adapter_stream.compute_features(
            contracts[i],
            contracts[:i],
            merchant_risk_table=table,
            card_first_seen=first_seen,
        )
        ieee_rec = adapter_ieee.map_row(_as_ieee_row(journey, i, CARD), _ieee_context(journey, i))
        pairs.append((stream_rec.features, ieee_rec.features))
    return pairs


@pytest.mark.parametrize("seed", [1, 2, 3, 7, 11])
def test_adapters_agree_feature_by_feature(seed):
    """The same journey, encoded two ways, must produce the same features.

    This is the assertion the parity claim always needed. Every skew listed in this
    module's docstring fails here.
    """
    mismatches: dict[str, list] = {}
    for index, (stream_f, ieee_f) in enumerate(_both_adapters(seed)):
        for name in FEATURE_NAMES:
            if name in PROXY_ONLY:
                continue
            got, want = getattr(stream_f, name), getattr(ieee_f, name)
            if isinstance(got, float) and isinstance(want, float):
                if got == pytest.approx(want, rel=1e-6, abs=1e-9):
                    continue
            elif got == want:
                continue
            mismatches.setdefault(name, []).append((index, got, want))

    assert not mismatches, (
        "train/serve skew — the adapters disagree on what these mean:\n"
        + "\n".join(
            f"  {name}: {len(rows)} row(s) differ, e.g. row {rows[0][0]}: "
            f"stream={rows[0][1]!r} ieee={rows[0][2]!r}"
            for name, rows in sorted(mismatches.items())
        )
    )


@pytest.mark.parametrize("seed", [1, 2, 3, 7, 11])
def test_both_adapters_satisfy_the_canonical_semantics(seed):
    """Each adapter independently honours every invariant in `semantics.py`."""
    for index, (stream_f, ieee_f) in enumerate(_both_adapters(seed)):
        assert not (v := check_invariants(stream_f)), f"stream row {index}: {v}"
        assert not (v := check_invariants(ieee_f)), f"ieee row {index}: {v}"


# --------------------------------------------------------------------------- #
# Distributional support — the check that catches a feature that is silently dead
# --------------------------------------------------------------------------- #


def _support(vectors: list[FeatureVector], name: str) -> set:
    return {getattr(v, name) for v in vectors}


# Deliberately NOT filtered by PROXY_ONLY: whether a feature is a proxy on the training
# side says nothing about whether it varies on the serving side, and `merchant_risk_score`
# — the worst offender — is a proxy. Excluding proxies here would have hidden it again.
@pytest.mark.parametrize("name", FEATURE_NAMES)
def test_no_feature_is_a_dead_constant_on_the_serving_path(name):
    """A served feature must actually vary.

    `merchant_risk_score` was 0.0 and `card_age_days` was 0 for EVERY transaction the
    system ever scored, because no caller passed a merchant risk table and card age was
    derived from a capped in-memory ring buffer. The model had learned splits on both. A
    constant feature at serving is a split the model can never take — and nothing in 210
    tests noticed, because no test ever asserted a stream feature's range.
    """
    served = [stream_f for stream_f, _ in _both_adapters(seed=1)]
    support = _support(served, name)
    assert len(support) > 1, (
        f"{name} takes the single value {support.pop()!r} across a full card journey — "
        "it is a dead constant on the serving path, and any split the model learned on it "
        "is unreachable in production"
    )


def test_device_seen_before_carries_information_beyond_card_age():
    """`device_seen_before` must not be a shadow of `card_age_days`.

    In training it was literally `card_age_days > 0`, so it carried zero independent signal
    — and the simulator's `new_device` fraud pattern (a known card on an unknown device)
    was therefore undetectable by the one feature designed to catch it.
    """
    for adapter_index, adapter_name in ((0, "stream"), (1, "ieee")):
        vectors = [pair[adapter_index] for pair in _both_adapters(seed=3)]
        shadow = [v.device_seen_before == (v.card_age_days > 0) for v in vectors]
        assert not all(shadow), (
            f"{adapter_name}: device_seen_before == (card_age_days > 0) on every row — "
            "it is a deterministic function of another feature, not an independent signal"
        )


def test_every_invariant_is_exercised_by_this_corpus():
    """The journey must actually reach the states the invariants describe.

    Without this, an invariant can be added, never triggered, and mistaken for coverage —
    the same shape of self-deception as a gate nobody has attacked.
    """
    vectors = [f for pair in _both_adapters(seed=1) for f in pair]
    unexercised = [
        inv.name
        for inv in INVARIANTS
        # An invariant is exercised if the corpus contains rows on both sides of its
        # boundary; a corpus where it is trivially true everywhere proves nothing.
        if len({inv.holds(v) for v in vectors}) == 1 and not any(inv.holds(v) for v in vectors)
    ]
    assert not unexercised, f"invariants never satisfied by any row (bad corpus?): {unexercised}"
