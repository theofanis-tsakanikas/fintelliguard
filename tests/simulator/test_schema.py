"""The generated records must match the bronze stream contract exactly."""

from __future__ import annotations

from datetime import datetime

from simulator import CONTRACT_FIELDS, SimulatorConfig, TransactionGenerator


def _records():
    gen = TransactionGenerator(SimulatorConfig(seed=1, fraud_injection_rate=0.2))
    return gen.generate(300)


def test_contract_dict_has_exactly_the_contract_fields():
    for txn in _records():
        contract = txn.to_contract_dict()
        assert tuple(contract.keys()) == CONTRACT_FIELDS


def test_field_types_match_the_contract():
    for txn in _records():
        c = txn.to_contract_dict()
        assert isinstance(c["transaction_id"], str)
        assert isinstance(c["timestamp"], str)
        # timestamp must be parseable ISO 8601.
        assert datetime.fromisoformat(c["timestamp"]).tzinfo is not None
        assert isinstance(c["amount"], float)
        assert c["amount"] > 0
        assert isinstance(c["merchant_id"], str)
        assert isinstance(c["card_hash"], str)
        assert isinstance(c["device_id"], str)
        assert isinstance(c["ip_country"], str) and len(c["ip_country"]) == 2
        assert isinstance(c["mcc_code"], str)
        assert len(c["mcc_code"]) == 4 and c["mcc_code"].isdigit()


def test_ground_truth_is_separated_from_the_contract():
    for txn in _records():
        contract = txn.to_contract_dict()
        assert "is_fraud_truth" not in contract
        assert "fraud_pattern" not in contract

        eval_record = txn.to_eval_dict()
        assert isinstance(eval_record["is_fraud_truth"], bool)
        # Every contract field is also present in the eval record.
        for field in CONTRACT_FIELDS:
            assert eval_record[field] == contract[field]
