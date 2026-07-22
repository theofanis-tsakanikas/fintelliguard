"""The copilot's demo transactions must match the Tier-2 online-feature store.

`agents/databricks/demo_transactions.json` feeds the SERVED copilot's get_fraud_score; the same
transactions are seeded into S3 by `infra/aws/online_features.tf` for the Bedrock Lambda. If the
two drift, the copilot and the real-time system score DIFFERENT features for the "same"
transaction id — the copilot would explain a flag the production path never produced. This
asserts the two sources are identical, id for id and feature for feature.
"""

from __future__ import annotations

import json
from pathlib import Path

import hcl2

_ROOT = Path(__file__).resolve().parents[2]
_JSON = _ROOT / "agents" / "databricks" / "demo_transactions.json"
_TF = _ROOT / "infra" / "aws" / "online_features.tf"


def _unwrap(value):
    return value[0] if isinstance(value, list) and len(value) == 1 else value


def _tf_online_features() -> dict:
    with open(_TF, encoding="utf-8") as handle:
        doc = hcl2.load(handle)
    for block in doc.get("locals", []):
        if "online_features" in block:
            return _unwrap(block["online_features"])
    raise AssertionError("locals.online_features not found in online_features.tf")


def test_copilot_demo_transactions_match_the_tier2_online_store():
    from_json = json.loads(_JSON.read_text(encoding="utf-8"))
    from_tf = _tf_online_features()

    assert set(from_json) == set(from_tf), "the two demo stores hold different transaction ids"

    for txn_id, features in from_json.items():
        tf_features = from_tf[txn_id]
        assert set(features) == set(tf_features), f"{txn_id}: different feature sets"
        for name, value in features.items():
            tf_value = tf_features[name]
            if isinstance(value, float) or isinstance(tf_value, float):
                assert abs(float(value) - float(tf_value)) < 1e-6, f"{txn_id}.{name} drifted"
            else:
                assert value == tf_value, f"{txn_id}.{name} differs: {value!r} vs {tf_value!r}"
