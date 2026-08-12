# ADR-0007: Streaming is a run-then-destroy stage, not a standing service

- **Status:** Accepted
- **Date:** 2026-06 (recorded 2026-08)

## Context

The architecture calls for real managed Kafka: MSK, in the customer-managed VPC, with IAM SASL auth
and TLS, reached privately by Databricks classic compute. Running it locally in Docker would prove
nothing about the part that is actually hard — the private cross-cloud path.

MSK is also the most expensive and slowest thing in the estate. It bills continuously at roughly
$0.11/hour, and — the part that hurts more — adds around 30 minutes to **every** apply and **every**
destroy. Left standing between demos it dominates both the bill and the iteration loop.

## Decision

Treat streaming as a **stage**, not a service. The `Deploy` workflow's `stage` input accepts
`all | network | train | serving | streaming`, and **`streaming` is deliberately not part of `all`**.
The MSK-dependent path is opt-in: you deploy it, run the probe and the scorer, capture the evidence,
and tear it down.

Correctness is proven by `ml/serving/msk_probe.py` rather than by uptime: it produces a uniquely
marked record from a Databricks cluster, reads it back at the exact `(partition, offset)`, and raises
loudly with security-group / instance-profile / PassRole diagnostics when the path is broken. A pass
is evidence the private path resolves; there is no need to keep it running afterwards to believe it.

The in-VPC ECS scorer deliberately carries a **bundled demo model**, so the streaming demo runs
before any model exists. The real Mosaic endpoint and the real Bedrock verdict are exercised by
`ml/serving/funnel.py`, not by that container.

## Alternatives rejected

- **MSK standing permanently.** Continuous cost for a demonstrator nobody is querying, and 30 minutes
  added to every teardown attempt.
- **Local Kafka in Docker for the cloud demo.** Cheap and fast, and proves nothing about IAM SASL,
  advertised listeners, or the security-group path that was the actual engineering problem.
- **Serverless MSK.** Removes the per-broker charge but not the provisioning time, and changes the
  auth path being demonstrated.

## Consequences

- The most expensive component is opt-in, which is what makes `layers: core` a genuinely cheaper
  deploy rather than a nominal one.
- Evidence of the streaming path is a probe result and a CloudWatch capture, not a live endpoint —
  and the README says so rather than implying a service is running.
- The MSK IAM warm-up subtlety (the token is only served during `poll()`, so the client must poll
  before producing and tolerate a mid-handshake `_TRANSPORT` raise) lives in code and is exercised by
  the probe, because there is no long-running consumer to hide it.
