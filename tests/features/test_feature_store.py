"""The Feature Store definition is a faithful, side-effect-free spec."""

from __future__ import annotations

from ml.features import FEATURE_NAMES
from ml.features.feature_store import FEATURE_TABLE
from ml.features.schema import LOOKUP_KEY, PRIMARY_KEY


def test_table_identity_and_keys():
    assert FEATURE_TABLE.full_name == "fintelliguard.features.txn_features"
    assert FEATURE_TABLE.primary_keys == (PRIMARY_KEY,)
    assert FEATURE_TABLE.lookup_key == LOOKUP_KEY
    assert FEATURE_TABLE.online is True
    assert FEATURE_TABLE.offline is True


def test_columns_are_keys_plus_canonical_features():
    assert FEATURE_TABLE.column_names() == (PRIMARY_KEY, LOOKUP_KEY, *FEATURE_NAMES)


def test_sql_types_map_correctly():
    by_name = {c.name: c for c in FEATURE_TABLE.columns}
    assert by_name[PRIMARY_KEY].sql_type == "STRING"
    assert by_name["amount_usd"].sql_type == "DOUBLE"
    assert by_name["txn_velocity_1h"].sql_type == "BIGINT"
    assert by_name["device_seen_before"].sql_type == "BOOLEAN"
    assert by_name[PRIMARY_KEY].is_key is True
    assert by_name["amount_usd"].is_key is False


def test_to_dict_round_trips_essentials():
    payload = FEATURE_TABLE.to_dict()
    assert payload["full_name"] == "fintelliguard.features.txn_features"
    assert payload["primary_keys"] == [PRIMARY_KEY]
    assert payload["lookup_key"] == LOOKUP_KEY
    assert len(payload["columns"]) == len(FEATURE_NAMES) + 2
