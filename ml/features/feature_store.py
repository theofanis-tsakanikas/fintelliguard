"""Feature Store table DEFINITION — spec only.

Describes the `fintelliguard.features.txn_features` table (online + offline) that the
canonical 15 features land in. This is a pure description used by tests and, later, by
the cloud registration step (Databricks Feature Engineering). Nothing here calls any
cloud API — registration is deferred to `ml/serving` / the bundles layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .schema import FEATURE_SPECS, LOOKUP_KEY, PRIMARY_KEY, FeatureSpec

# Canonical Python type -> Databricks/Delta SQL type.
_SQL_TYPES: dict[type, str] = {float: "DOUBLE", int: "BIGINT", bool: "BOOLEAN", str: "STRING"}


def _sql_type(dtype: type) -> str:
    return _SQL_TYPES[dtype]


@dataclass(frozen=True)
class Column:
    """One column of the feature table."""

    name: str
    sql_type: str
    is_key: bool = False


def _columns() -> tuple[Column, ...]:
    keys = (
        Column(PRIMARY_KEY, _sql_type(str), is_key=True),
        Column(LOOKUP_KEY, _sql_type(str), is_key=True),
    )
    features = tuple(Column(spec.name, _sql_type(spec.dtype)) for spec in FEATURE_SPECS)
    return keys + features


@dataclass(frozen=True)
class FeatureTableSpec:
    """Declarative spec for the online+offline feature table (no side effects)."""

    full_name: str
    primary_keys: tuple[str, ...]
    lookup_key: str
    online: bool
    offline: bool
    columns: tuple[Column, ...]
    description: str

    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)

    def to_dict(self) -> dict[str, Any]:
        return {
            "full_name": self.full_name,
            "primary_keys": list(self.primary_keys),
            "lookup_key": self.lookup_key,
            "online": self.online,
            "offline": self.offline,
            "columns": [
                {"name": c.name, "type": c.sql_type, "is_key": c.is_key} for c in self.columns
            ],
            "description": self.description,
        }


# The single feature table definition.
FEATURE_TABLE = FeatureTableSpec(
    full_name="fintelliguard.features.txn_features",
    primary_keys=(PRIMARY_KEY,),
    lookup_key=LOOKUP_KEY,
    online=True,  # <5ms lookup by card_hash at inference
    offline=True,  # point-in-time joins for MLflow training
    columns=_columns(),
    description="Canonical 15 fraud features; PK transaction_id, online lookup by card_hash.",
)


def feature_specs() -> tuple[FeatureSpec, ...]:
    """The canonical feature specs this table stores (re-exported for convenience)."""
    return FEATURE_SPECS
