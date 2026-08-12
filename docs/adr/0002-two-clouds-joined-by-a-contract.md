# ADR-0002: Two clouds, joined by a contract rather than coupling

- **Status:** Accepted
- **Date:** 2026-06 (recorded 2026-08)

## Context

All three tiers could run on Databricks alone. Mosaic AI provides model serving, an agent framework
and vector search; one platform means one bill, one IAM model, and no cross-cloud integration to
build or debug.

The counter-argument is that the deployment this models does not look like that. In a real bank the
real-time edge sits next to the payment gateway — in AWS — while the lakehouse and its data science
sit in Databricks. Two teams, two platforms, two release cadences.

## Decision

Run the real-time regulated verdict (Tier 2) in **AWS Bedrock**, at the edge, and the lakehouse plus
the async copilot (Tiers 1 and 3) in **Databricks** — and join them with a single narrow interface:

```
get_fraud_score(transaction) → {fraud_score, model_version, threshold, decision_hint, top_features}
```

Bedrock reaches that function through a VPC-internal Lambda on a least-privilege role, over private
connectivity, with a Databricks OAuth token fetched from Secrets Manager at call time. **Bedrock
never reads Delta, never sees raw data, and never knows Mosaic internals.**

## Alternatives rejected

- **Everything on Databricks.** Simpler in every operational sense, and the honest recommendation for
  a greenfield project with no AWS footprint. Rejected because it dissolves the trust boundary — the
  agent would sit inside the lakehouse with access to the data it is supposed to reason *about*, not
  *over* — and because the cross-cloud integration is itself part of what this project demonstrates.
- **A shared Delta table as the interface.** Would couple the two platforms to a schema and give
  Bedrock read access to the lakehouse. A function signature is a smaller promise than a table.
- **A public API endpoint with a static key.** Rejected outright: a standing credential on a path
  that carries payment data.

## Consequences

- The trust boundary is narrow, explicit and testable — the contract has its own document
  (`docs/bedrock-integration.md`) and its own tests.
- Feature parity becomes non-negotiable: the same 14 features must be computed identically at
  training and at serving, proven by a distributional parity test.
- The cost is real: two IAM models, two state layers, private networking to build, and an MSK IAM
  auth subtlety that had to be handled in code.
- A failure in the contract is a failure of the whole funnel, which is why `ml/serving/msk_probe.py`
  exists and raises loudly with SG / instance-profile / PassRole diagnostics rather than timing out.
