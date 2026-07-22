"""The served copilot as an MLflow pyfunc — the thin Databricks-backed wrapper around the
framework-agnostic `CopilotModel`.

`copilot_model.CopilotModel` is the pure, unit-tested orchestration (score + retrieve +
synthesise). This module is the part that can only run on Databricks: it builds the three
collaborators from live endpoints at serving load, and adapts them to the interfaces the
orchestration expects. It is deliberately NOT unit-tested against real services — the seams it
fills are exercised by the deploy's smoke test, and each live call degrades gracefully so a
single unavailable dependency yields a partial brief rather than a 500.

Serving-time auth is automatic: the model is logged (see `infra/bundles/register_copilot.py`)
declaring the fraud-score endpoint, the LLM endpoint and the vector index as `resources`, and
Databricks Model Serving injects a scoped credential for exactly those.

Config (endpoint/index names) travels as a JSON artifact logged with the model, so the served
model has no hard-coded workspace specifics.
"""

from __future__ import annotations

import json
from typing import Any

import mlflow.pyfunc
import pandas as pd

from agents.databricks.copilot_model import CopilotModel, build_investigation_prompt
from agents.databricks.tools.get_fraud_score import FraudScoreTool
from agents.databricks.tools.search_similar_cases import RESULT_COLUMNS, SimilarCaseSearch

CONFIG_ARTIFACT = "config"
FEATURES_ARTIFACT = "online_features"


class _DeployClientScorer:
    """Calls the fraud-score serving endpoint through the mlflow deployments client."""

    def __init__(self, client: Any, endpoint: str) -> None:
        self._client = client
        self._endpoint = endpoint

    def score(self, features: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.predict(
            endpoint=self._endpoint, inputs={"dataframe_records": [features]}
        )
        preds = resp["predictions"] if isinstance(resp, dict) else resp
        return preds[0]


class _DeployClientLLM:
    """Single-shot synthesis via a Databricks foundation-model endpoint."""

    def __init__(self, client: Any, endpoint: str) -> None:
        self._client = client
        self._endpoint = endpoint

    def __call__(self, prompt: str) -> str:
        resp = self._client.predict(
            endpoint=self._endpoint,
            inputs={"messages": [{"role": "user", "content": prompt}], "max_tokens": 512},
        )
        # chat-completions shape
        return resp["choices"][0]["message"]["content"]


class CopilotPyfunc(mlflow.pyfunc.PythonModel):
    def load_context(self, context) -> None:
        cfg = json.loads(open(context.artifacts[CONFIG_ARTIFACT]).read())
        features_table = json.loads(open(context.artifacts[FEATURES_ARTIFACT]).read())

        from databricks.vector_search.client import VectorSearchClient
        from mlflow.deployments import get_deploy_client

        deploy = get_deploy_client("databricks")

        # get_fraud_score: resolve id -> features from the bundled table, then score live.
        def lookup(transaction_id: str, card_hash: str) -> dict[str, Any]:
            record = features_table.get(transaction_id) or {}
            return {k: v for k, v in record.items() if k != "card_hash"}

        scorer = _DeployClientScorer(deploy, cfg["fraud_endpoint"])
        self._score_tool = FraudScoreTool(scorer.score, lookup)

        # search_similar_cases: the live vector index (auth via declared resource).
        vsc = VectorSearchClient(disable_notice=True)
        index = vsc.get_index(endpoint_name=cfg["vector_endpoint"], index_name=cfg["vector_index"])
        self._search_tool = SimilarCaseSearch(index, columns=RESULT_COLUMNS)

        self._llm = _DeployClientLLM(deploy, cfg["llm_endpoint"])
        self._copilot = CopilotModel(self._score_tool, self._search_tool, self._llm)

    def _investigate_row(self, row: pd.Series) -> dict[str, Any]:
        query = str(row.get("query") or "Investigate this flagged transaction.")
        txn = str(row.get("transaction_id") or "")
        card = str(row.get("card_hash") or "")

        # Degrade per-tool: a single unavailable dependency yields a partial brief, never a 500.
        score: dict[str, Any] = {}
        cases: list[dict[str, Any]] = []
        errors: list[str] = []
        try:
            score = self._score_tool.get_fraud_score(txn, card)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"get_fraud_score: {type(exc).__name__}")
        try:
            cases = self._search_tool.search(query, num_results=int(row.get("num_cases") or 5))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"search_similar_cases: {type(exc).__name__}")
        try:
            answer = self._llm(build_investigation_prompt(query, score, cases))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"llm: {type(exc).__name__}")
            answer = _fallback_summary(score, cases)

        return {
            "answer": answer,
            "fraud_score": score.get("fraud_score"),
            "decision_hint": score.get("decision_hint"),
            "top_features": score.get("top_features", []),
            "similar_cases": cases,
            "tools_used": [
                t for t, ok in [("get_fraud_score", score), ("search_similar_cases", cases)] if ok
            ],
            "errors": errors,
        }

    def predict(self, context, model_input, params=None) -> list[str]:
        """One JSON-string investigation brief per input row.

        A JSON string (not a nested dict) keeps the Unity Catalog signature simple — UC
        REQUIRES a signature to register, and a string output column is unambiguous where a
        nested/variable brief is not."""
        if isinstance(model_input, dict):
            model_input = pd.DataFrame([model_input])
        elif isinstance(model_input, list):
            model_input = pd.DataFrame(model_input)
        return [
            json.dumps(self._investigate_row(row), default=str) for _, row in model_input.iterrows()
        ]


def _fallback_summary(score: dict[str, Any], cases: list[dict[str, Any]]) -> str:
    """A deterministic brief when the LLM endpoint is unavailable — the analyst still gets the
    score and the precedents, which are the load-bearing evidence."""
    parts = []
    if score:
        parts.append(f"Model score {score.get('fraud_score')} -> {score.get('decision_hint')}.")
    if cases:
        parts.append(f"{len(cases)} similar resolved case(s) retrieved for comparison.")
    return " ".join(parts) or "No evidence could be gathered for this transaction."
