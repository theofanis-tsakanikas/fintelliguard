"""Agent Evaluation — labeled dataset + local tool-selection scoring harness.

The full Mosaic AI Agent Evaluation (LLM-judge correctness/groundedness/relevance) runs on
Databricks and is deferred. Tool-selection accuracy is scorable locally and lives here.
"""

from __future__ import annotations

from agents.databricks.eval.dataset import EvalCase, eval_dataset
from agents.databricks.eval.scoring import (
    ToolSelectionReport,
    keyword_router,
    score_tool_selection,
)

__all__ = [
    "EvalCase",
    "ToolSelectionReport",
    "eval_dataset",
    "keyword_router",
    "score_tool_selection",
]
