"""Databricks Mosaic AI analyst copilot — Tier-3 (logic layer).

Tool implementations and the evaluation harness are locally testable. The agent's LLM
tool-routing, Vector Search embedding, Genie NL->SQL, and LLM-judge evaluation run only
on Databricks and are deferred. Copilot infra (Vector Search index, agent serving
endpoint, Genie space) is the next step.
"""
