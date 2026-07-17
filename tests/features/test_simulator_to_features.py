"""End-to-end: simulator output through adapter_stream must surface injected fraud.

Proves the injected patterns are learnable in the canonical feature space.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from ml.features import validate_feature_vector
from ml.features.adapter_stream import compute_features
from ml.features.semantics import check_invariants
from simulator import SimulatorConfig, TransactionGenerator


def _materialize():
    gen = TransactionGenerator(SimulatorConfig(seed=11, fraud_injection_rate=0.15, n_cards=300))
    txns = gen.generate(3000)

    history: dict[str, list[dict]] = defaultdict(list)
    first_seen: dict[str, str] = {}
    rows = []  # (pattern, feature_vector, prior_count)
    for txn in txns:
        current = txn.to_contract_dict()
        card = txn.card_hash
        prior_count = sum(1 for h in history[card] if h["timestamp"] < current["timestamp"])
        first_seen.setdefault(card, current["timestamp"])
        record = compute_features(
            current,
            history[card],
            card_first_seen=datetime.fromisoformat(first_seen[card]),
        )
        validate_feature_vector(record.features)  # every emitted vector stays in contract
        assert not check_invariants(record.features)  # ...and stays semantically coherent
        rows.append((txn.fraud_pattern, record.features, prior_count))
        history[card].append(current)
    return rows


ROWS = _materialize()


def _where(pattern: str):
    return [(fv, pc) for p, fv, pc in ROWS if p == pattern]


def test_velocity_spike_drives_high_velocity():
    velocities = [fv.txn_velocity_1h for fv, _ in _where("velocity_spike")]
    assert velocities
    assert max(velocities) >= 5


def test_country_mismatch_flag_set_when_history_exists():
    with_history = [fv for fv, pc in _where("country_mismatch") if pc > 0]
    assert len(with_history) >= 5
    flagged = sum(1 for fv in with_history if fv.country_mismatch)
    assert flagged / len(with_history) >= 0.9


def test_amount_outlier_produces_high_zscore():
    zscores = [fv.amount_zscore for fv, pc in _where("amount_outlier") if pc >= 3]
    assert zscores
    assert max(zscores) > 3.0


def test_unusual_hour_is_a_strong_signal_but_history_can_normalise_it():
    """Night fraud is flagged — except on cards that already transact at night.

    This asserted `all(flags)`, which was only true because `is_unusual_hour` used a
    linear [min, max] band over a circular quantity. The correct circular version does not
    flag a 03:00 transaction on a card whose own history contains 02:00 activity — and
    repeated night fraud on one card puts exactly that in its history. That is the feature
    working: "unusual" is relative to the card, so a card habituated to the small hours has
    no unusual hour there, and no amount of labelling makes it one.

    So the honest assertion is signal, not absolutes: strongly enriched on the archetype,
    and rare on legitimate traffic.
    """
    fraud_flags = [fv.is_unusual_hour for fv, _ in _where("unusual_hour")]
    legit_flags = [fv.is_unusual_hour for fv, _ in _where(None)]
    assert fraud_flags and legit_flags

    fraud_rate = sum(fraud_flags) / len(fraud_flags)
    legit_rate = sum(legit_flags) / len(legit_flags)
    assert fraud_rate > 0.8, f"the archetype is barely detectable: {fraud_rate:.2f}"
    assert fraud_rate > legit_rate * 5, (
        f"unusual-hour fraud ({fraud_rate:.2f}) is not meaningfully separated from "
        f"legitimate traffic ({legit_rate:.2f})"
    )


def test_new_device_not_seen_before():
    flags = [fv.device_seen_before for fv, _ in _where("new_device")]
    assert flags
    assert all(seen is False for seen in flags)


def test_legit_transactions_do_not_trip_country_mismatch():
    # Legit traffic always uses the card's home country, so the geography flag — the
    # crispest fraud signal — should essentially never fire on it.
    legit = [fv for p, fv, _ in ROWS if p is None]
    assert legit
    flagged = sum(1 for fv in legit if fv.country_mismatch)
    assert flagged / len(legit) < 0.02
