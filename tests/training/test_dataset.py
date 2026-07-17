"""Dataset prep — feature parity with ml/features and no label leakage."""

from __future__ import annotations

import pytest

from ml.features.schema import FEATURE_NAMES
from ml.training.dataset import (
    FEATURE_COLUMNS,
    LABEL_COLUMN,
    make_synthetic_frame,
    prepare_xy,
)


def test_feature_columns_are_the_canonical_fifteen():
    assert FEATURE_COLUMNS == list(FEATURE_NAMES)
    # Derived, never hardcoded: a literal count drifts from the list it counts —
    # exactly how README's "199 tests" outlived a 210-test suite.
    assert len(FEATURE_COLUMNS) == len(FEATURE_NAMES)
    assert LABEL_COLUMN not in FEATURE_COLUMNS


def test_prepare_xy_uses_exactly_canonical_features_without_label():
    frame = make_synthetic_frame(n_rows=120, seed=1)
    x, y = prepare_xy(frame)
    assert list(x.columns) == list(FEATURE_NAMES)
    assert LABEL_COLUMN not in x.columns
    assert len(x) == len(y) == 120
    # All features numeric (bools coerced to 0/1).
    assert all(str(dtype) == "float64" for dtype in x.dtypes)


def test_prepare_xy_rejects_missing_columns():
    frame = make_synthetic_frame(n_rows=20, seed=2).drop(columns=["amount_zscore"])
    with pytest.raises(ValueError, match="missing columns"):
        prepare_xy(frame)


def test_synthetic_frame_has_both_classes():
    frame = make_synthetic_frame(n_rows=400, seed=3)
    assert set(frame[LABEL_COLUMN].unique()) == {0, 1}
