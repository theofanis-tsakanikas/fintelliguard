"""Feature layer — the parity contract.

A single canonical 15-feature schema, shared pure transforms, and two adapters that must
both produce that schema (stream for serving, IEEE-CIS for training). See
`docs/features.md`.
"""

from __future__ import annotations

from . import adapter_ieee, adapter_stream, transforms
from .feature_store import FEATURE_TABLE, FeatureTableSpec
from .schema import (
    FEATURE_NAMES,
    FEATURE_SPECS,
    LOOKUP_KEY,
    PRIMARY_KEY,
    FeatureRecord,
    FeatureSpec,
    FeatureVector,
    validate_feature_vector,
)

__all__ = [
    "FEATURE_NAMES",
    "FEATURE_SPECS",
    "FEATURE_TABLE",
    "LOOKUP_KEY",
    "PRIMARY_KEY",
    "FeatureRecord",
    "FeatureSpec",
    "FeatureTableSpec",
    "FeatureVector",
    "adapter_ieee",
    "adapter_stream",
    "transforms",
    "validate_feature_vector",
]
