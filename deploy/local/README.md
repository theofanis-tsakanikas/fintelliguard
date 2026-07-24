# Local end-to-end funnel — one command, no cloud

The pure transforms, the scorer, the verdict gate and the guardrail are all excellent and
unit-tested — but until now nothing wired them into a *running* funnel off Databricks, and
the Prometheus/Grafana observability was dashboard-JSON only (no metrics emitter). This
brings the whole thing to life locally:

```bash
make e2e        # docker compose up --build
# ... then open Grafana at http://localhost:3000  (admin / admin)
make e2e-down   # stop + clean up
```

## What runs

```
simulator ──▶ Kafka (txn.raw) ──▶ streaming scorer ──▶ Prometheus ──▶ Grafana
                                   │
                                   ├─ features  (ml.features.adapter_stream — the SAME adapter Gold uses)
                                   ├─ Tier 1    (ml.serving.FraudScorer — real XGBoost + TreeSHAP)
                                   └─ Tier 2    (agents.bedrock.eval.judge + guardrails.policy — REAL gates)
```

- **`kafka`** — single-node KRaft broker, topic `txn.raw` auto-created.
- **`simulator`** — `python -m simulator --sink kafka` at ~30 txns/sec, realistic ~1% fraud.
- **`scorer`** — `ml.serving.stream_service`: trains a demo XGBoost from the simulator on
  startup (`ml.serving.local_model` — the local analogue of the deferred IEEE-CIS bootstrap),
  then consumes the topic, computes the 14 features, scores + explains (TreeSHAP), runs
  flagged cases through the real verdict gate + output guardrail, and exposes `/metrics` on
  `:8000` with the exact series the dashboards query.
- **`prometheus`** — scrapes the scorer.
- **`grafana`** — provisioned from the committed `dashboards/`. Open **"FintelliGuard — Local
  Funnel (no cloud)"**: it queries the `fintelliguard_*` / `model_serving_*` series the local
  scorer actually emits (score distribution, throughput by decision, latency, verdict-gate
  outcomes, guardrail/quarantine/log-refusal counters) and is fully populated. The four
  production dashboards target the real cloud metric taxonomy (`dlt_pipeline_up`,
  `kafka_consumergroup_lag`, `bedrock_verdict_duration_seconds`, Databricks SQL) — of those,
  only **Serving & Latency** lights up locally; the DLT / Kafka-exporter / Bedrock / Databricks
  panels stay empty because no such component runs here. Every dashboard's `environment`
  variable now defaults to `local` (dropdown still offers `dev` / `prod`).

## Honest scope

- **Real:** the feature adapter (parity with Gold), the XGBoost scorer + TreeSHAP, the 5-check
  verdict-acceptance gate, the output guardrail, and the Prometheus metrics.
- **Stubbed:** the Tier-2 **reasoner**. A live run would call the AWS Bedrock agent (wired default: Amazon Nova Lite; Claude Haiku once Anthropic-approved) to *write*
  the compliance verdict; here `stream_service.build_stub_verdict` synthesises a well-formed
  verdict from the scorer output so the real gate + guardrail have something to judge. The
  live Bedrock path is deferred to the AWS deploy.
- **Not in the local stack:** the Databricks DLT medallion, Mosaic Model Serving, the Tier-3
  copilot, and the online Feature Store lookup (all cloud-deferred).

The funnel *logic* (feature computation, scoring, the gates, the metric names) is unit-tested
offline in `tests/serving/test_local_runtime.py` — `make e2e` is the visual, running form.
