"""A fixed seed must reproduce the exact same stream."""

from __future__ import annotations

from simulator import SimulatorConfig, TransactionGenerator


def _eval_stream(seed: int, n: int = 500):
    gen = TransactionGenerator(SimulatorConfig(seed=seed, fraud_injection_rate=0.1))
    return [txn.to_eval_dict() for txn in gen.generate(n)]


def test_same_seed_is_deterministic():
    assert _eval_stream(seed=7) == _eval_stream(seed=7)


def test_different_seed_differs():
    assert _eval_stream(seed=7) != _eval_stream(seed=8)
