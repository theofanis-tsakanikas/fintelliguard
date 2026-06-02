"""Fraud injection rate and the per-pattern raw-field signatures."""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime

from simulator import FraudPattern, SimulatorConfig, TransactionGenerator
from simulator.generator import UNUSUAL_HOURS


def _by_pattern(txns, pattern: FraudPattern):
    return [t for t in txns if t.fraud_pattern == pattern.value]


def test_fraud_rate_respected_within_tolerance():
    rate = 0.05
    n = 20000
    gen = TransactionGenerator(SimulatorConfig(seed=3, fraud_injection_rate=rate))
    txns = gen.generate(n)
    observed = sum(1 for t in txns if t.is_fraud_truth) / n
    assert abs(observed - rate) < 0.01


def test_velocity_spike_clusters_timestamps_for_one_card():
    gen = TransactionGenerator(SimulatorConfig(seed=5, fraud_injection_rate=0.5))
    txns = gen.generate(5000)
    by_card: dict[str, list[datetime]] = defaultdict(list)
    for t in _by_pattern(txns, FraudPattern.VELOCITY_SPIKE):
        by_card[t.card_hash].append(datetime.fromisoformat(t.timestamp))

    busiest = max(by_card.values(), key=len)
    assert len(busiest) >= 3
    busiest.sort()
    deltas = [(b - a).total_seconds() for a, b in zip(busiest, busiest[1:])]
    # Each consecutive pair for the card is seconds apart — a tight cluster.
    assert max(deltas) <= 15.0


def test_country_mismatch_differs_from_home_while_legit_matches():
    gen = TransactionGenerator(SimulatorConfig(seed=6, fraud_injection_rate=0.3))
    txns = gen.generate(4000)

    mismatches = _by_pattern(txns, FraudPattern.COUNTRY_MISMATCH)
    assert mismatches
    for t in mismatches:
        assert t.ip_country != gen.home_country(t.card_hash)

    # Legitimate transactions always use the card's home country.
    for t in txns:
        if not t.is_fraud_truth:
            assert t.ip_country == gen.home_country(t.card_hash)


def test_amount_outlier_is_far_above_typical():
    gen = TransactionGenerator(SimulatorConfig(seed=8, fraud_injection_rate=0.3))
    txns = gen.generate(4000)

    outliers = [t.amount for t in _by_pattern(txns, FraudPattern.AMOUNT_OUTLIER)]
    legit = [t.amount for t in txns if not t.is_fraud_truth]
    assert outliers and legit
    assert statistics.median(outliers) > 5 * statistics.median(legit)


def test_unusual_hour_is_nocturnal_and_disjoint_from_legit():
    gen = TransactionGenerator(SimulatorConfig(seed=9, fraud_injection_rate=0.3))
    txns = gen.generate(4000)

    unusual = _by_pattern(txns, FraudPattern.UNUSUAL_HOUR)
    assert unusual
    for t in unusual:
        assert datetime.fromisoformat(t.timestamp).hour in UNUSUAL_HOURS

    for t in txns:
        if not t.is_fraud_truth:
            assert datetime.fromisoformat(t.timestamp).hour not in UNUSUAL_HOURS


def test_new_device_is_unseen_for_the_card():
    gen = TransactionGenerator(SimulatorConfig(seed=10, fraud_injection_rate=0.3))
    txns = gen.generate(4000)

    new_device = _by_pattern(txns, FraudPattern.NEW_DEVICE)
    assert new_device
    for t in new_device:
        assert t.device_id not in gen.known_devices(t.card_hash)
        assert t.device_id.startswith("D-new-")
