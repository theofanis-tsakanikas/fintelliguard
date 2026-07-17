"""Both adapters must emit EXACTLY the canonical schema (names, types, ranges).

Scope note, because this file used to overreach: it checks the SHAPE of the contract. It
does not check that the two adapters mean the same thing by it — that is
`test_parity_distributional.py`, which runs one card journey through both encodings and
compares the values.

The difference matters. This file once carried the whole weight of "feature parity is
non-negotiable" with `assert type(stream_row[name]) is type(ieee_row[name])`, which cannot
fail: both adapters build the same frozen dataclass. Six real skews lived under it.
"""

from __future__ import annotations

import dataclasses

from ml.features import FEATURE_NAMES, FEATURE_SPECS, validate_feature_vector
from ml.features.adapter_ieee import CardContext, map_row
from ml.features.adapter_stream import compute_features
from ml.features.merchant_risk import build_merchant_risk_table
from ml.features.schema import LOOKUP_KEY, PRIMARY_KEY, FeatureVector

RISK_TABLE = build_merchant_risk_table(
    [{"merchant_id": "M001", "is_fraud": i % 8 == 0} for i in range(100)]
)

CURRENT = {
    "transaction_id": "t1",
    "timestamp": "2026-01-01T12:00:00+00:00",
    "amount": 50.0,
    "merchant_id": "M001",
    "card_hash": "cardA",
    "device_id": "D001",
    "ip_country": "DE",
    "mcc_code": "5411",
}

IEEE_ROW = {
    "TransactionID": 1001,
    "card1": 1234,
    "TransactionAmt": 120.0,
    "C1": 3,
    "C2": 10,
    "C4": 4,
    "C6": 2,
    "D1": 45,
    "addr2": 87.0,
    "dist1": 50.0,
    "ProductCD": "C",
    "TransactionDT": 3 * 3600 + 5,
}


def test_feature_vector_matches_canonical_specs():
    field_names = tuple(f.name for f in dataclasses.fields(FeatureVector))
    assert field_names == FEATURE_NAMES
    assert tuple(s.name for s in FEATURE_SPECS) == FEATURE_NAMES


def test_stream_adapter_emits_canonical_schema_in_range():
    record = compute_features(CURRENT, [], merchant_risk_table=RISK_TABLE)
    assert tuple(record.features.as_dict().keys()) == FEATURE_NAMES
    validate_feature_vector(record.features)  # raises if out of contract
    assert tuple(record.to_row().keys()) == (PRIMARY_KEY, LOOKUP_KEY, *FEATURE_NAMES)


def test_ieee_adapter_emits_canonical_schema_in_range():
    record = map_row(IEEE_ROW, CardContext(amount_mean=100.0, amount_std=20.0, modal_addr2=60.0))
    assert tuple(record.features.as_dict().keys()) == FEATURE_NAMES
    validate_feature_vector(record.features)
    assert tuple(record.to_row().keys()) == (PRIMARY_KEY, LOOKUP_KEY, *FEATURE_NAMES)


# NOTE: there was a `test_..._refuses_to_run_without_a_merchant_risk_table` here that did
# `with pytest.raises(TypeError): compute_features(CURRENT, [])`. `merchant_risk_table` is a
# required keyword-only argument, so that TypeError comes from CPython's argument binding —
# the test asserted that Python works. `scripts/gate_proof.py` caught it: the attack that
# makes the parameter optional again left the test green. The real guard against the dead
# feature is test_parity_distributional.py::
# test_no_feature_is_a_dead_constant_on_the_serving_path.
