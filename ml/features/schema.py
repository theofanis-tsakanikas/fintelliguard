"""Canonical 15-feature schema — the single source of truth for feature parity.

Both adapters (`adapter_stream`, `adapter_ieee`) MUST produce exactly this schema: same
names, same types, same valid ranges (see `docs/features.md`). Anything else is
training/serving skew. `validate_feature_vector` is the gate both adapters are tested
against.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class FeatureSpec:
    """Name, type and valid range of one canonical feature."""

    name: str
    dtype: type
    minimum: float | None = None
    maximum: float | None = None
    exclusive_min: bool = False
    exclusive_max: bool = False
    allowed: tuple[int, ...] | None = None

    def check(self, value: Any) -> str | None:
        """Return an error message if `value` is invalid, else None."""
        if value is None:
            return f"{self.name}: null not allowed"
        if not _type_ok(self.dtype, value):
            return f"{self.name}: expected {self.dtype.__name__}, got {type(value).__name__}"
        if self.allowed is not None and value not in self.allowed:
            return f"{self.name}: {value} not in {self.allowed}"
        if self.minimum is not None:
            if self.exclusive_min and not value > self.minimum:
                return f"{self.name}: {value} must be > {self.minimum}"
            if not self.exclusive_min and not value >= self.minimum:
                return f"{self.name}: {value} must be >= {self.minimum}"
        if self.maximum is not None:
            if self.exclusive_max and not value < self.maximum:
                return f"{self.name}: {value} must be < {self.maximum}"
            if not self.exclusive_max and not value <= self.maximum:
                return f"{self.name}: {value} must be <= {self.maximum}"
        return None


def _type_ok(dtype: type, value: Any) -> bool:
    # bool is a subclass of int — keep them strictly distinct.
    if dtype is bool:
        return isinstance(value, bool)
    if dtype is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if dtype is float:
        return isinstance(value, float)
    return isinstance(value, dtype)


# The 15 features, in the grouping order of docs/features.md. Ranges come from the DLT
# validation gates in that doc.
FEATURE_SPECS: tuple[FeatureSpec, ...] = (
    # Amount (3)
    FeatureSpec(
        "amount_usd",
        float,
        minimum=0.0,
        maximum=1_000_000.0,
        exclusive_min=True,
        exclusive_max=True,
    ),
    FeatureSpec("amount_log", float, minimum=0.0),
    FeatureSpec("amount_zscore", float),  # unbounded by design
    # Velocity (4)
    FeatureSpec("txn_velocity_1h", int, minimum=0),
    FeatureSpec("txn_velocity_24h", int, minimum=0),
    FeatureSpec("amount_sum_1h", float, minimum=0.0),
    FeatureSpec("distinct_merchants_24h", int, minimum=0),
    # Identity & device (3)
    FeatureSpec("card_age_days", int, minimum=0),
    FeatureSpec("device_seen_before", bool),
    FeatureSpec("device_txn_count_24h", int, minimum=0),
    # Geography (2)
    FeatureSpec("country_mismatch", bool),
    FeatureSpec("distinct_countries_24h", int, minimum=0),
    # Merchant (2)
    FeatureSpec("merchant_risk_score", float, minimum=0.0, maximum=1.0),
    FeatureSpec("mcc_risk_tier", int, allowed=(1, 2, 3, 4, 5)),
    # Temporal (1)
    FeatureSpec("is_unusual_hour", bool),
)

FEATURE_NAMES: tuple[str, ...] = tuple(spec.name for spec in FEATURE_SPECS)

# Feature-store keys (not features themselves).
PRIMARY_KEY = "transaction_id"
LOOKUP_KEY = "card_hash"


@dataclass(frozen=True)
class FeatureVector:
    """The canonical 15 features for one transaction."""

    amount_usd: float
    amount_log: float
    amount_zscore: float
    txn_velocity_1h: int
    txn_velocity_24h: int
    amount_sum_1h: float
    distinct_merchants_24h: int
    card_age_days: int
    device_seen_before: bool
    device_txn_count_24h: int
    country_mismatch: bool
    distinct_countries_24h: int
    merchant_risk_score: float
    mcc_risk_tier: int
    is_unusual_hour: bool

    def as_dict(self) -> dict[str, Any]:
        """Features in canonical order."""
        return asdict(self)


@dataclass(frozen=True)
class FeatureRecord:
    """A feature vector plus its store keys."""

    transaction_id: str
    card_hash: str
    features: FeatureVector

    def to_row(self) -> dict[str, Any]:
        """Flat row: keys + the 15 features, ready for the feature table."""
        return {
            PRIMARY_KEY: self.transaction_id,
            LOOKUP_KEY: self.card_hash,
            **self.features.as_dict(),
        }


def validate_feature_vector(features: FeatureVector) -> None:
    """Raise ValueError if any feature is out of contract (type, range, or monotonicity)."""
    values = features.as_dict()
    errors = [msg for spec in FEATURE_SPECS if (msg := spec.check(values[spec.name])) is not None]
    # Cross-field gate from docs/features.md: 24h velocity must dominate 1h velocity.
    if features.txn_velocity_24h < features.txn_velocity_1h:
        errors.append("txn_velocity_24h must be >= txn_velocity_1h")
    if errors:
        raise ValueError("; ".join(errors))
