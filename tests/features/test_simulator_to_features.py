"""End-to-end: simulator output through adapter_stream must surface injected fraud.

Proves the injected patterns are learnable in the canonical feature space.
"""

from __future__ import annotations

from collections import defaultdict

from ml.features import validate_feature_vector
from ml.features.adapter_stream import compute_features
from simulator import SimulatorConfig, TransactionGenerator


def _materialize():
    gen = TransactionGenerator(SimulatorConfig(seed=11, fraud_injection_rate=0.15, n_cards=300))
    txns = gen.generate(3000)
    history: dict[str, list[dict]] = defaultdict(list)
    rows = []  # (pattern, feature_vector, prior_count)
    for txn in txns:
        current = txn.to_contract_dict()
        prior_count = sum(
            1 for h in history[txn.card_hash] if h["timestamp"] < current["timestamp"]
        )
        record = compute_features(current, history[txn.card_hash])
        validate_feature_vector(record.features)  # every emitted vector stays in contract
        rows.append((txn.fraud_pattern, record.features, prior_count))
        history[txn.card_hash].append(current)
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


def test_unusual_hour_flag_set():
    flags = [fv.is_unusual_hour for fv, _ in _where("unusual_hour")]
    assert flags
    assert all(flags)


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
