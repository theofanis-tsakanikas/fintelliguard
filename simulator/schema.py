"""The bronze stream contract emitted by the simulator.

These eight fields ARE the contract the bronze layer enforces (see
`docs/data-flow.md`: topic `txn.raw`). The 15 Gold features are derived downstream — the
simulator only emits raw events.

Ground-truth labels (`is_fraud_truth`, `fraud_pattern`) are kept strictly separate: they
are produced for evaluation/demo only and are excluded from `to_contract_dict()`, which
is the model-facing payload. They must never be fed to the model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# The exact bronze stream contract — field order is significant for readability only.
CONTRACT_FIELDS: tuple[str, ...] = (
    "transaction_id",
    "timestamp",
    "amount",
    "merchant_id",
    "card_hash",
    "device_id",
    "ip_country",
    "mcc_code",
)

# Eval-only fields, never part of the contract / model input.
LABEL_FIELDS: tuple[str, ...] = ("is_fraud_truth", "fraud_pattern")


@dataclass(frozen=True)
class Transaction:
    """A single synthetic transaction.

    The first eight attributes are the bronze contract. `is_fraud_truth` and
    `fraud_pattern` are evaluation-only ground truth, separated from the contract.
    """

    transaction_id: str
    timestamp: str  # ISO 8601, UTC (e.g. "2026-01-01T13:45:30+00:00")
    amount: float
    merchant_id: str
    card_hash: str
    device_id: str
    ip_country: str  # ISO 3166-1 alpha-2
    mcc_code: str  # 4-digit merchant category code, kept as string (preserves leading 0)

    # Ground truth — eval/demo only, never sent to the model.
    is_fraud_truth: bool | None = None
    fraud_pattern: str | None = None

    def to_contract_dict(self) -> dict[str, Any]:
        """Return only the bronze contract fields (the model-facing payload)."""
        return {
            "transaction_id": self.transaction_id,
            "timestamp": self.timestamp,
            "amount": self.amount,
            "merchant_id": self.merchant_id,
            "card_hash": self.card_hash,
            "device_id": self.device_id,
            "ip_country": self.ip_country,
            "mcc_code": self.mcc_code,
        }

    def to_eval_dict(self) -> dict[str, Any]:
        """Return the contract fields plus the separated ground-truth labels."""
        record = self.to_contract_dict()
        record["is_fraud_truth"] = self.is_fraud_truth
        record["fraud_pattern"] = self.fraud_pattern
        return record
