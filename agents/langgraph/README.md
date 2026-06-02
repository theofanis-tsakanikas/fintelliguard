# agents/langgraph/ — self-healing (Supervisor + Medic)

Operational resilience for the pipeline (NARRATIVE.md: "self-healing over manual"). A
**LangGraph** watches health signals, classifies incidents, and applies **deterministic
remediation** for known failure classes. The reasoning agents are consumers; this layer
operates.

> **What runs where.** The **graph orchestration + decision logic** are tested locally
> with real LangGraph and mocked signals/actions. **Live monitoring** (real DLT API,
> Kafka lag, endpoint metrics) and **real remediation** are **cloud-deferred**.

## Graph

```
START → collect (monitors) → supervisor (classify) → ┬ medic → END
                                                     └ END (healthy)
```

- `state.py` — graph state: signals, classified incident, decisions (audit), actions
  taken (prevents double-remediation), retry counts.
- `monitors.py` — health collectors behind **injectable** clients: DLT pipeline state,
  Kafka consumer lag, Model Serving p99 latency. Real clients are cloud-deferred.
- `supervisor.py` — classifies signals (healthy / degraded / failed) by priority
  (pipeline failure > endpoint latency > consumer lag) and routes to Medic or end.
- `medic.py` — deterministic remediation for **known** classes, via **injectable**
  actions:
  - endpoint **p99 > 200 ms** → roll back to the previous model version (MLflow client),
  - consumer **lag > threshold** → scale consumers / alert,
  - **pipeline failure** → retry up to a bound, then escalate,
  - **unknown** → LLM root-cause (injectable, mocked) then escalate.
  Remediation is **idempotent** — an incident already acted on is not remediated twice.
- `graph.py` — assembles + compiles the LangGraph. **LangSmith tracing is configurable
  and OFF by default** (no live calls in tests).
- `config.py` — thresholds (p99 200 ms, lag, retry count) per NARRATIVE/PROJECT_PLAN.

## Local testing

```bash
pytest tests/agents/langgraph     # supervisor routing, medic per-class, graph e2e, idempotency
ruff check .
```

Tests use real LangGraph with mocked monitors (fixed signal dicts), a fake MLflow client
(asserts the rollback promotes the previous version), and recorder actions. Tracing is
off; the LLM root-cause node is mocked (known-class remediation needs no LLM).

## Deferred to deploy

Wiring the monitors to live clients (Databricks DLT API, MSK/Kafka consumer-group lag,
Model Serving latency metrics), executing real remediation (model rollback, consumer
scaling, pipeline restart, on-call escalation), the real LLM root-cause for unknown
incidents, and LangSmith trace export — all run against the live platform.
