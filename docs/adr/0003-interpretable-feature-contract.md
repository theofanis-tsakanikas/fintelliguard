# ADR-0003: A 14-feature interpretable contract, not the anonymised columns

- **Status:** Accepted
- **Date:** 2026-06 (recorded 2026-08)

## Context

The IEEE-CIS dataset ships 339 anonymised engineered columns (`V1`–`V339`) alongside the raw
transaction fields. Using them is the obvious path to a higher AUC, and it is what the competition
leaderboard rewarded.

It is also unusable here. A compliance analyst reading a Tier-2 verdict has to be told *why* a
transaction was flagged, and "V257 was high" is not a reason anyone can act on or defend to a
regulator. Worse, the anonymised columns cannot be computed online — nobody knows what they are.

## Decision

Declare a compact contract of **14 features**, each of which is (a) explainable in one sentence to a
non-technical reader and (b) computable at inference time from data the edge actually has:

| Group | Count | Examples |
|---|---:|---|
| Amount | 3 | `amount_usd`, `amount_log`, `amount_zscore` |
| Velocity | 4 | `txn_velocity_1h`, `distinct_merchants_24h` |
| Identity & device | 3 | `card_age_days`, `device_seen_before` |
| Geography | 2 | `country_mismatch`, `distinct_countries_24h` |
| Merchant | 1 | `mcc_risk_tier` |
| Temporal | 1 | `is_unusual_hour` |

The canonical count is `len(FEATURE_SPECS)` in `ml/features/schema.py` — a number derived from the
code, not asserted in a document. Two adapters (`adapter_ieee` for training, `adapter_stream` for
serving) must produce the same 14 semantic features, and a distributional parity test proves it.

The scorer returns per-transaction **TreeSHAP** contributions as `top_features`, so a verdict cites
*why this transaction*, not global importance.

## Alternatives rejected

- **`V1`–`V339`.** Higher AUC, no explainability, not computable online. Everything downstream —
  the verdict gate's faithfulness check, the copilot's risk drivers, the model card — depends on
  features having meaning.
- **A 15th feature, `merchant_risk_score`.** Genuinely useful, and **removed rather than faked**: it
  needs merchant identity *and* labels in one dataset, which no source available here has. Shipping
  it as a constant `0.0` would have inflated the feature count while contributing nothing — and it
  did, briefly, until `make gate-proof` was built and caught it.

## Consequences

- Ceiling accepted: the compact contract tops out near AUC 0.85 on real IEEE-CIS, which is why the
  promotion gate sits at 0.83 rather than a number borrowed from leaderboard write-ups
  ([ADR-0004](0004-promotion-gate-thresholds.md)).
- Feature parity is a hard constraint: any feature change touches `ml/training/` and both adapters in
  the same commit, or the parity test fails.
- The faithfulness check in the verdict gate is only possible *because* features have names: it
  asserts the agent's declared drivers are a subset of the model's actual `top_features`.
