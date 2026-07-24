# simulator/

Python transaction generator (~**500 txns/sec**) producing synthetic payment events for
the streaming path. Pure Python, deterministic, fully unit-tested locally — **no
infrastructure required to run**.

## What it emits

The **bronze stream contract** (`docs/data-flow.md`, topic `txn.raw`) — exactly these
eight fields: `transaction_id`, `timestamp`, `amount`, `merchant_id`, `card_hash`,
`device_id`, `ip_country`, `mcc_code`. The 14 Gold features are derived downstream.

Ground-truth labels (`is_fraud_truth`, `fraud_pattern`) are **eval/demo only** and kept
out of the model-facing payload (`Transaction.to_contract_dict()`); they appear only in
`to_eval_dict()`. Never feed them to the model.

## Fraud injection

Five realistic patterns, each expressed in raw fields so they survive into the features:

| Pattern | Raw signal | Feeds feature(s) |
|---|---|---|
| `velocity_spike` | many txns for one card, seconds apart | velocity, amount_sum |
| `country_mismatch` | `ip_country` ≠ card's home country | country_mismatch |
| `amount_outlier` | `amount` ≫ card's typical | amount_zscore |
| `unusual_hour` | timestamp hour in the dead of night | is_unusual_hour |
| `new_device` | `device_id` never seen for the card | device_seen_before |

## Run it

```bash
# 20 records to stdout, reproducible
python -m simulator --max-records 20 --seed 7

# paced JSONL file, 1% fraud, 5 seconds
python -m simulator --rate 500 --duration 5 --fraud-rate 0.01 --jsonl out.jsonl

# production Kafka path (no eval labels); broker from env, never hardcoded
KAFKA_BOOTSTRAP_SERVERS=localhost:9092 \
  python -m simulator --sink kafka --no-ground-truth --duration 10
```

Flags: `--rate`, `--duration`, `--max-records`, `--fraud-rate`, `--seed`, `--sink`
(`local`|`kafka`), `--jsonl`, `--no-ground-truth`.

## Layout

`schema.py` (contract) · `config.py` (typed config, Kafka from env) · `generator.py`
(seeded generation + fraud) · `sinks.py` (`Sink` interface, `LocalSink`, `KafkaSink`) ·
`runner.py` (paced loop) · `cli.py` / `__main__.py`.

The Kafka sink is config-gated and its producer is injectable, so it unit-tests against a
mock — no broker needed. In dev, point it at local Kafka (Docker); MSK is reserved for
integration testing and the demo.

## Test

```bash
pytest tests/simulator        # schema, determinism, fraud rate, signatures, sinks
ruff check .
```
