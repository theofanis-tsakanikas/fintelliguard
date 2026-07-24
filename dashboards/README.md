# dashboards/

**Grafana** dashboards (JSON) + data source provisioning. The JSON definitions and the
panel SQL are **locally testable**; live Grafana rendering against real data sources is
**deferred**.

## Dashboards (`grafana/`)

| File | Concern | Data source(s) |
|---|---|---|
| `fraud_overview.json` | Fraud score distribution, decision mix, Tier-2 escalation rate, top merchant/country risk | Databricks SQL (gold) |
| `pipeline_health.json` | DLT pipeline status, Kafka consumer lag, silver throughput, quarantine counts | Prometheus + Databricks SQL |
| `serving_latency.json` | `get_fraud_score` p99/p50 latency, request rate, served model version | Prometheus |
| `compliance.json` | Bedrock verdict counts/latency, guardrail block rate | Databricks SQL + Prometheus |

Each dashboard has an `$environment` template variable and references the declared data
sources by uid (`fintelliguard-databricks`, `fintelliguard-prometheus`). Databricks-SQL
panels target the `gold`/`silver` schema this project builds; PromQL panels target
serving/infra metrics.

## Provisioning (`provisioning/`)

- `datasources.yaml` — the Databricks SQL Warehouse + Prometheus data sources. **No
  secrets**: credentials are env references (`${DATABRICKS_HOST}`,
  `${DATABRICKS_SQL_HTTP_PATH}`, `${DATABRICKS_TOKEN}`, `${PROMETHEUS_URL}`) that Grafana
  interpolates at load.
- `dashboards.yaml` — file provider that loads `grafana/*.json`.

## Local testing

```bash
pytest tests/dashboards      # JSON structure, required panels, SQL-vs-gold, PromQL, no-secrets
ruff check .
```

- Every dashboard JSON parses and conforms (panels, datasource refs, `$environment`).
- The **Databricks-SQL panel queries execute against a local-Spark gold/silver sample**
  whose `gold.txn_features_realtime` uses the exact canonical 14-feature schema — so the
  dashboards are cross-checked against the real gold schema (Grafana's `$__timeFilter`
  macro is rewritten to a no-op predicate for local execution).
- PromQL panels are structurally validated (no Prometheus locally).
- Provisioning YAML is checked to contain no literal secrets.

## Deferred

Live Grafana rendering, real Databricks SQL Warehouse / Prometheus connections, and
dashboard import into a running Grafana instance run against the deployed platform.
