"""The served analyst copilot — orchestration core (framework-agnostic, fully testable).

Tier 3 is a human-driven investigation tool. An analyst opens a flagged transaction and the
copilot returns an investigation brief: WHY the model flagged it (the live get_fraud_score,
the same endpoint Bedrock uses) and PRECEDENT — semantically similar resolved cases from the
Vector Search index — synthesized into a natural-language summary by an LLM.

This module is the orchestration ONLY: the three collaborators are injected.
  - `score_tool`  : agents.databricks.tools.get_fraud_score.FraudScoreTool (id -> features -> score)
  - `search_tool` : agents.databricks.tools.search_similar_cases.SimilarCaseSearch (vector index)
  - `llm`         : a callable prompt -> str (a Databricks foundation-model endpoint at serving)
So the whole routing/synthesis is unit-testable with stubs, and the pyfunc wrapper
(`copilot_pyfunc.py`) only has to build the real Databricks-backed collaborators.

`query_lakehouse` (Genie NL->SQL) is deliberately NOT wired here: it needs a SQL warehouse and
a Genie space, neither of which the current deploy provisions. It is the honest deferral —
declared in `agents/databricks/agent.py` and documented in `docs/copilot-design.md`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol


class _ScoreTool(Protocol):
    def get_fraud_score(self, transaction_id: str, card_hash: str) -> dict[str, Any]: ...


class _SearchTool(Protocol):
    def search(self, query: str, *, num_results: int = 5) -> list[dict[str, Any]]: ...


def build_investigation_prompt(
    query: str, score: dict[str, Any], cases: list[dict[str, Any]]
) -> str:
    """Assemble the analyst-facing synthesis prompt from the tool evidence.

    Kept pure and separate so the exact instruction the LLM receives is testable and
    reviewable — the copilot's summary is only as grounded as the evidence block it is given.
    """
    drivers = ", ".join(
        f"{feat.get('name')}={feat.get('value')} (contribution {feat.get('contribution')})"
        for feat in score.get("top_features", [])
    )
    precedent = (
        "\n".join(
            f"- case {c.get('case_id', '?')}: outcome={c.get('outcome', '?')}, "
            f"disposition={c.get('disposition', '?')} — {c.get('summary', '')}"
            for c in cases
        )
        or "- (no similar resolved cases retrieved)"
    )
    return (
        "You are a fraud-analysis copilot. Using ONLY the evidence below, write a concise "
        "investigation brief for the analyst: what the model saw, how it compares to precedent, "
        "and a recommended next step. Do not invent facts not in the evidence.\n\n"
        f"Analyst question: {query}\n\n"
        f"Model score: fraud_score={score.get('fraud_score')} "
        f"decision_hint={score.get('decision_hint')} threshold={score.get('threshold')}\n"
        f"Top drivers: {drivers or '(none)'}\n\n"
        f"Similar resolved cases:\n{precedent}\n"
    )


class CopilotModel:
    """Orchestrates the copilot's tools + an LLM synthesis into an investigation brief."""

    def __init__(
        self,
        score_tool: _ScoreTool,
        search_tool: _SearchTool,
        llm: Callable[[str], str],
    ) -> None:
        self._score_tool = score_tool
        self._search_tool = search_tool
        self._llm = llm

    def investigate(
        self,
        *,
        query: str,
        transaction_id: str,
        card_hash: str,
        num_cases: int = 5,
    ) -> dict[str, Any]:
        """Return {answer, fraud_score, decision_hint, top_features, similar_cases, tools_used}.

        The score and the retrieval are REAL tool calls (live model + live vector index); the
        answer is the LLM's grounded synthesis of them. Every field the LLM summarised is also
        returned raw, so the analyst can audit the summary against the evidence.
        """
        if not query:
            raise ValueError("the copilot needs an analyst question to investigate")

        score = self._score_tool.get_fraud_score(transaction_id, card_hash)
        cases = self._search_tool.search(query, num_results=num_cases)
        answer = self._llm(build_investigation_prompt(query, score, cases))

        return {
            "answer": answer,
            "fraud_score": score.get("fraud_score"),
            "decision_hint": score.get("decision_hint"),
            "top_features": score.get("top_features", []),
            "similar_cases": cases,
            "tools_used": ["get_fraud_score", "search_similar_cases"],
        }
