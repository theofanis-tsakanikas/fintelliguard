"""Bronze transforms: contract parsing + schema rescue, and IEEE row_hash/metadata."""

from __future__ import annotations

import json

from pipelines.bronze.bronze_transforms import parse_ieee_raw, parse_transactions_stream

_GOOD = {
    "transaction_id": "t1",
    "timestamp": "2026-01-01T12:00:00+00:00",
    "amount": 50.0,
    "merchant_id": "M1",
    "card_hash": "0" * 32,
    "device_id": "D1",
    "ip_country": "DE",
    "mcc_code": "5411",
}


def test_parse_transactions_stream_parses_and_rescues(spark):
    raw = spark.createDataFrame(
        [(json.dumps(_GOOD), 10), ("{not valid json", 11)], ["value", "offset"]
    )
    out = parse_transactions_stream(raw)

    assert set(out.columns) == {
        "transaction_id",
        "timestamp",
        "amount",
        "merchant_id",
        "card_hash",
        "device_id",
        "ip_country",
        "mcc_code",
        "_rescued_data",
        "ingest_timestamp",
        "source",
        "offset",
    }
    by_offset = {r["offset"]: r.asDict() for r in out.collect()}

    good = by_offset[10]
    assert good["transaction_id"] == "t1"
    assert good["amount"] == 50.0
    assert good["source"] == "kafka:txn.raw"
    assert good["_rescued_data"] is None

    bad = by_offset[11]
    assert bad["transaction_id"] is None  # unparseable -> null contract fields
    assert bad["_rescued_data"] == "{not valid json"  # raw payload preserved


def test_parse_ieee_raw_adds_rowhash_and_metadata(spark):
    raw = spark.createDataFrame(
        [(1, 1234, 100.0), (1, 1234, 100.0), (2, 5678, 20.0)],
        ["TransactionID", "card1", "TransactionAmt"],
    )
    out = parse_ieee_raw(raw)
    assert {"row_hash", "ingest_timestamp", "source", "_rescued_data"} <= set(out.columns)

    rows = out.collect()
    hashes = [r["row_hash"] for r in rows]
    assert all(h is not None for h in hashes)
    # Identical content rows hash identically; a different row hashes differently.
    assert hashes[0] == hashes[1]
    assert hashes[0] != hashes[2]
    assert {r["source"] for r in rows} == {"autoloader:ieee-cis"}
