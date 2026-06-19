# Dataset Card — FintelliGuard Training Data

> Generated from code by `python -m ml.governance.generate`. Do not edit by hand.

## Sources

- **IEEE-CIS Fraud Detection** (public benchmark) — adapted to the canonical 15-feature schema.
- **Synthetic transaction stream** — the simulator (`simulator/`), ~500 txns/s, with injected fraud archetypes; adapted by `ml/features/adapter_stream.py`.

## Schema & parity

One canonical schema of **15 features** (`ml/features/schema.py`). Both adapters must emit it identically — a single end-to-end test compares them, eliminating training/serving skew by construction.

## Personal data handling

- Card identifiers are hashed (`card_hash`); no raw PAN, name, or email enters the feature store.
- The Tier-2 agent's guardrail anonymises `CREDIT_DEBIT_CARD_NUMBER`, `NAME`, `EMAIL` on I/O; the verdict gate rejects any raw PII in output.

## Labels

- Binary fraud label, used only as the training target — never as a model input.

