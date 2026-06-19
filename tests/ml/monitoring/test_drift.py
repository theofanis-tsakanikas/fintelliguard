"""Tests for the PSI / KS drift detector."""

import numpy as np
import pytest

from ml.monitoring.drift import (
    PSI_SIGNIFICANT,
    STATUS_DRIFT,
    STATUS_STABLE,
    classify_psi,
    compute_drift,
    ks_statistic,
    population_stability_index,
)


def _rng():
    return np.random.default_rng(0)


def test_identical_distributions_psi_near_zero():
    data = _rng().normal(0, 1, 5000).tolist()
    assert population_stability_index(data, data) < 0.01


def test_shifted_distribution_psi_high():
    rng = _rng()
    ref = rng.normal(0, 1, 5000).tolist()
    cur = rng.normal(3, 1, 5000).tolist()
    assert population_stability_index(ref, cur) >= PSI_SIGNIFICANT


def test_ks_identical_near_zero_shifted_high():
    rng = _rng()
    ref = rng.normal(0, 1, 5000).tolist()
    assert ks_statistic(ref, ref) == 0.0
    cur = rng.normal(3, 1, 5000).tolist()
    assert ks_statistic(ref, cur) > 0.5


def test_classify_bands():
    assert classify_psi(0.05) == STATUS_STABLE
    assert classify_psi(0.30) == STATUS_DRIFT


def test_compute_drift_flags_only_drifted_feature():
    rng = _rng()
    ref = {
        "amount_usd": rng.normal(100, 20, 3000).tolist(),
        "txn_velocity_1h": rng.poisson(2, 3000).tolist(),
    }
    cur = {
        "amount_usd": rng.normal(170, 30, 3000).tolist(),  # drifts
        "txn_velocity_1h": rng.poisson(2, 3000).tolist(),  # stable
    }
    report = compute_drift(ref, cur)
    assert report.status == STATUS_DRIFT
    alerts = {f.feature for f in report.alerts}
    assert alerts == {"amount_usd"}
    assert "status" in report.to_dict()


def test_low_cardinality_feature_does_not_crash():
    # boolean-like feature collapses quantile bins; must still produce a finite PSI.
    ref = [0] * 900 + [1] * 100
    cur = [0] * 500 + [1] * 500
    psi = population_stability_index(ref, cur)
    assert psi > 0 and np.isfinite(psi)


def test_empty_inputs_raise():
    with pytest.raises(ValueError):
        population_stability_index([], [1.0])
    with pytest.raises(ValueError):
        compute_drift({"a": [1.0]}, {"b": [1.0]})  # no shared features
