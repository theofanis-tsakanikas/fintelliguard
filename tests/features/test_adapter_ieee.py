"""IEEE-CIS proxy mappings on small synthetic, hand-built rows (no real dataset)."""

from __future__ import annotations

import math

from ml.features import validate_feature_vector
from ml.features.adapter_ieee import CardContext, map_row
from ml.features.semantics import check_invariants


def test_proxy_mapping_on_known_row():
    row = {
        "TransactionID": 1001,
        "card1": 1234,
        "TransactionAmt": 120.0,
        "C1": 3,  # -> txn_velocity_1h
        "C2": 10,  # -> txn_velocity_24h
        "C4": 4,  # -> distinct_merchants_24h
        "C6": 2,  # -> device_txn_count_24h
        "D1": 45,  # -> card_age_days
        "addr2": 87.0,  # -> country (vs modal)
        "dist1": 50.0,  # -> distinct_countries proxy
        "ProductCD": "C",  # -> merchant risk / tier
        "TransactionDT": 3 * 3600 + 5,  # -> hour 3 (night)
    }
    ctx = CardContext(
        amount_mean=100.0, amount_std=20.0, modal_addr2=60.0, active_hours=(9, 10, 11)
    )
    record = map_row(row, ctx)
    fv = record.features

    assert record.transaction_id == "1001"
    assert record.card_hash == "1234"
    assert fv.amount_usd == 120.0
    assert fv.amount_log == math.log1p(120.0)
    assert fv.amount_zscore == (120.0 - 100.0) / 20.0
    assert fv.txn_velocity_1h == 3
    assert fv.txn_velocity_24h == 10
    # Current amount + the 2 other in-window transactions valued at the card's mean.
    # Was `C1 * amount`, which returns 0.0 when C1 is 0 — a state the serving path, whose
    # sum always contains the current amount, cannot reach.
    assert fv.amount_sum_1h == 120.0 + 2 * 100.0
    assert fv.distinct_merchants_24h == 4
    assert fv.card_age_days == 45
    # C6 = 2 > 1: this device transacted before within 24h. Was `card_age_days > 0`,
    # which made the feature a pure function of feature #8 and carried no signal.
    assert fv.device_seen_before is True
    assert fv.device_txn_count_24h == 2
    assert fv.country_mismatch is True  # addr2 87 != modal 60
    assert fv.distinct_countries_24h == 2  # dist1 > 0
    assert fv.mcc_risk_tier == 5  # ProductCD C
    assert fv.is_unusual_hour is True  # hour 3 is far from the card's 9-11 active band
    validate_feature_vector(fv)


def test_velocity_monotonicity_is_enforced_when_proxy_inverts():
    # C1 > C2 in the raw data must not violate the 24h >= 1h gate.
    row = {
        "TransactionID": 2,
        "card1": 9,
        "TransactionAmt": 10.0,
        "C1": 10,
        "C2": 3,
        "TransactionDT": 12 * 3600,
        "ProductCD": "W",
    }
    fv = map_row(row).features
    assert fv.txn_velocity_1h == 10
    assert fv.txn_velocity_24h == 10  # max(C2, C1)
    assert fv.txn_velocity_24h >= fv.txn_velocity_1h
    validate_feature_vector(fv)


def test_neutral_defaults_without_context_or_optional_columns():
    row = {
        "TransactionID": 3,
        "card1": 7,
        "TransactionAmt": 25.0,
        "D1": 0,  # never seen
        "dist1": 0.0,  # single location
        "ProductCD": "Z",  # unknown -> defaults
        "TransactionDT": 12 * 3600,  # midday
    }
    fv = map_row(row).features
    assert fv.amount_zscore == 0.0  # no context stats
    assert fv.device_seen_before is False  # D1 == 0
    assert fv.country_mismatch is False  # no modal addr2 / no addr2
    assert fv.distinct_countries_24h == 1  # dist1 == 0
    assert fv.mcc_risk_tier == 2  # default
    assert fv.is_unusual_hour is False  # midday
    validate_feature_vector(fv)


def test_nan_cells_are_treated_as_missing_not_crashes():
    """A NaN in a sparse IEEE-CIS column must not kill the card group.

    `_num` guarded None, TypeError and ValueError — but a genuine `float('nan')` passed
    straight through the try/except, and the `int(...)` calls then raised `ValueError:
    cannot convert float NaN to integer`, failing the whole `applyInPandas` task and, after
    four stage retries, the DLT update. The supposed defence in `silver_transforms` uses
    `coalesce`, which replaces NULL — NaN is not NULL. IEEE-CIS's C/D/dist columns are
    famously sparse, so this was reachable with the real dataset on day one.
    """
    row = {
        "TransactionID": 7,
        "card1": 42,
        "TransactionAmt": 80.0,
        "C1": float("nan"),
        "C2": float("nan"),
        "C4": float("nan"),
        "C6": float("nan"),
        "D1": float("nan"),
        "dist1": float("nan"),
        "ProductCD": "W",
        "TransactionDT": 12 * 3600,
    }
    record = map_row(row, CardContext())  # must not raise
    validate_feature_vector(record.features)
    # Missing counts fall back to the window floor, not to a value the serving path
    # cannot produce.
    assert record.features.txn_velocity_1h == 1
    assert record.features.card_age_days == 0


def test_raw_zero_counts_are_floored_to_the_window_convention():
    """Raw IEEE C-counts bottom out at 0; the canonical window convention starts at 1.

    `semantics.py` fixes one rule — every window count includes the current transaction —
    and the stream adapter honours it with `len(within_1h) + 1`. Mapping `int(C1)` straight
    through trained the model on a "no activity at all" bucket that the serving path can
    never produce, so a split learned at `txn_velocity_1h <= 0.5` was dead code in
    production.

    This has to be asserted on RAW rows: the parity corpus encodes its C-counts under the
    canonical convention, so the floor is never exercised there.
    """
    row = {
        "TransactionID": 9,
        "card1": 7,
        "TransactionAmt": 120.0,
        "C1": 0.0,  # raw IEEE-CIS really does contain zeros here
        "C2": 0.0,
        "C4": 0.0,
        "C6": 0.0,
        "ProductCD": "W",
        "TransactionDT": 12 * 3600,
    }
    fv = map_row(row, CardContext()).features
    assert not check_invariants(fv), "a raw zero-count row violates the window convention"
    assert fv.txn_velocity_1h == 1
    assert fv.distinct_merchants_24h == 1
    assert fv.device_txn_count_24h == 1
    # ...and the amount window still contains the current amount.
    assert fv.amount_sum_1h >= fv.amount_usd


def test_device_seen_before_falls_back_to_device_activity_not_card_age():
    """With no device history from the batch job, fall back to C6 — never to card age.

    `device_seen_before = card_age_days > 0` made feature #9 a deterministic function of
    feature #8: zero independent signal at training, so the model never learned the
    new-device signature the simulator injects as a fraud archetype.
    """
    base = {
        "TransactionID": 11,
        "card1": 7,
        "TransactionAmt": 50.0,
        "D1": 400.0,  # a very old card...
        "C6": 1.0,  # ...whose device has NOT transacted before in the window
        "ProductCD": "W",
        "TransactionDT": 12 * 3600,
    }
    fv = map_row(base, CardContext()).features
    assert fv.card_age_days == 400
    assert fv.device_seen_before is False, (
        "an old card on a fresh device must not report device_seen_before — that is the "
        "new-device fraud signature, and tying it to card age erases it"
    )

    seen = map_row({**base, "C6": 4.0}, CardContext()).features
    assert seen.device_seen_before is True
