"""Feature-distribution drift detection for the fraud model — pure, dependency-light.

A trustworthy model is monitored: training-time feature distributions are the reference,
and live serving traffic is compared against them so silent data drift (which degrades the
score without any error) is caught before it harms decisions. This module computes the two
standard detectors per feature — Population Stability Index (PSI) and the two-sample
Kolmogorov–Smirnov statistic (KS) — with NumPy only (no SciPy), so it runs anywhere the
serving code does.

Thresholds follow the conventional PSI bands:

    PSI < 0.10        stable
    0.10 <= PSI < 0.25 minor shift (watch)
    PSI >= 0.25       significant drift (alert)

Maps to Readiness Framework dimension 3 (Observability & drift): *data drift and
model/output-quality drift are monitored, with thresholds; alerting fires before a user
reports it.*
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

PSI_MINOR = 0.10
PSI_SIGNIFICANT = 0.25

STATUS_STABLE = "stable"
STATUS_MINOR = "minor"
STATUS_DRIFT = "drift"

_EPS = 1e-6


def _as_array(values: Sequence[float]) -> np.ndarray:
    arr = np.asarray(list(values), dtype=float)
    return arr[~np.isnan(arr)]


def population_stability_index(
    reference: Sequence[float], current: Sequence[float], *, bins: int = 10
) -> float:
    """PSI of ``current`` vs ``reference`` using quantile bins of the reference.

    Constant reference (all one value) falls back to a single bin → PSI 0 unless the
    current mass moves off that value.
    """
    ref = _as_array(reference)
    cur = _as_array(current)
    if ref.size == 0 or cur.size == 0:
        raise ValueError("reference and current must be non-empty")

    # Quantile edges from the reference; collapse duplicates (low-cardinality features).
    quantiles = np.linspace(0, 1, bins + 1)
    edges = np.unique(np.quantile(ref, quantiles))
    if edges.size < 2:
        # Degenerate reference (single value): two bins around that value.
        v = edges[0]
        edges = np.array([-np.inf, v, np.inf])
    else:
        edges = edges.copy()
        edges[0] = -np.inf
        edges[-1] = np.inf

    ref_counts, _ = np.histogram(ref, bins=edges)
    cur_counts, _ = np.histogram(cur, bins=edges)

    ref_pct = ref_counts / ref.size
    cur_pct = cur_counts / cur.size

    ref_pct = np.clip(ref_pct, _EPS, None)
    cur_pct = np.clip(cur_pct, _EPS, None)

    return float(np.sum((cur_pct - ref_pct) * np.log(cur_pct / ref_pct)))


def ks_statistic(reference: Sequence[float], current: Sequence[float]) -> float:
    """Two-sample Kolmogorov–Smirnov statistic (max CDF gap), NumPy-only."""
    ref = np.sort(_as_array(reference))
    cur = np.sort(_as_array(current))
    if ref.size == 0 or cur.size == 0:
        raise ValueError("reference and current must be non-empty")

    grid = np.concatenate([ref, cur])
    cdf_ref = np.searchsorted(ref, grid, side="right") / ref.size
    cdf_cur = np.searchsorted(cur, grid, side="right") / cur.size
    return float(np.max(np.abs(cdf_ref - cdf_cur)))


def classify_psi(psi: float) -> str:
    if psi >= PSI_SIGNIFICANT:
        return STATUS_DRIFT
    if psi >= PSI_MINOR:
        return STATUS_MINOR
    return STATUS_STABLE


@dataclass(frozen=True)
class FeatureDrift:
    """Drift metrics for one feature."""

    feature: str
    psi: float
    ks: float
    status: str

    @property
    def alert(self) -> bool:
        return self.status == STATUS_DRIFT


@dataclass(frozen=True)
class DriftReport:
    """Per-feature drift plus an overall verdict."""

    features: tuple[FeatureDrift, ...]

    @property
    def alerts(self) -> tuple[FeatureDrift, ...]:
        return tuple(f for f in self.features if f.alert)

    @property
    def status(self) -> str:
        if any(f.status == STATUS_DRIFT for f in self.features):
            return STATUS_DRIFT
        if any(f.status == STATUS_MINOR for f in self.features):
            return STATUS_MINOR
        return STATUS_STABLE

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "alert_count": len(self.alerts),
            "features": [
                {
                    "feature": f.feature,
                    "psi": round(f.psi, 4),
                    "ks": round(f.ks, 4),
                    "status": f.status,
                }
                for f in self.features
            ],
        }


def compute_drift(
    reference: Mapping[str, Sequence[float]],
    current: Mapping[str, Sequence[float]],
    *,
    bins: int = 10,
) -> DriftReport:
    """Compute PSI + KS per feature for the features present in both mappings."""
    shared = [name for name in reference if name in current]
    if not shared:
        raise ValueError("no shared features between reference and current")
    feats = []
    for name in shared:
        psi = population_stability_index(reference[name], current[name], bins=bins)
        ks = ks_statistic(reference[name], current[name])
        feats.append(FeatureDrift(name, psi, ks, classify_psi(psi)))
    return DriftReport(tuple(feats))
