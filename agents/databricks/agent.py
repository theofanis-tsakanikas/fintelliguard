"""Mosaic AI Agent Framework copilot definition (framework-agnostic logic layer).

Declares the three tools with NAMES + DESCRIPTIONS + input schemas, plus the routing
system prompt. Tool descriptions are first-class engineering: routing quality depends on
them. The actual LLM tool-routing runs on Databricks (deferred); here we build the
declarative agent so it is testable and ready to register with the Agent Framework.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

_INSTRUCTIONS = Path(__file__).resolve().parent / "instructions" / "copilot_v1.md"

QUERY_LAKEHOUSE = "query_lakehouse"
SEARCH_SIMILAR_CASES = "search_similar_cases"
GET_FRAUD_SCORE = "get_fraud_score"


@dataclass(frozen=True)
class ToolSpec:
    """A tool the agent can route to: name + (first-class) description + input schema."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class CopilotAgent:
    """The declarative copilot: its tools and routing system prompt."""

    tools: tuple[ToolSpec, ...]
    system_prompt: str

    def tool_names(self) -> list[str]:
        return [tool.name for tool in self.tools]

    def get_tool(self, name: str) -> ToolSpec:
        for tool in self.tools:
            if tool.name == name:
                return tool
        raise KeyError(f"unknown tool: {name}")


def build_tools() -> tuple[ToolSpec, ...]:
    """The three copilot tools with carefully written, route-distinguishing descriptions."""
    return (
        ToolSpec(
            name=QUERY_LAKEHOUSE,
            description=(
                "Run precise, structured queries over the governed Unity Catalog tables for "
                "EXACT facts — counts, sums, rates, and history (e.g. a merchant's historical "
                "fraud rate, a card's transaction count, how many cards used a device). Use "
                "whenever the answer is a specific number or structured record. Do NOT use for "
                "similarity or 'cases like this'."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "A natural-language question answerable with exact facts.",
                    }
                },
                "required": ["question"],
            },
        ),
        ToolSpec(
            name=SEARCH_SIMILAR_CASES,
            description=(
                "Retrieve historically resolved fraud cases SEMANTICALLY SIMILAR to the current "
                "one (features, analyst notes, outcome). Use for 'cases like this one', "
                "precedent, or pattern-matching by similarity. Do NOT use for exact counts."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Description of the current case to find precedents for.",
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "How many similar cases to return.",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        ),
        ToolSpec(
            name=GET_FRAUD_SCORE,
            description=(
                "Return the model's fraud score and the per-transaction features that drove it "
                "(the same get_fraud_score the real-time system uses). Use to explain WHY a "
                "transaction was flagged."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "transaction_id": {
                        "type": "string",
                        "description": "The transaction identifier.",
                    },
                    "card_hash": {"type": "string", "description": "The card lookup key."},
                },
                "required": ["transaction_id", "card_hash"],
            },
        ),
    )


def system_prompt() -> str:
    """The versioned copilot routing system prompt."""
    return _INSTRUCTIONS.read_text(encoding="utf-8")


def build_agent() -> CopilotAgent:
    """Assemble the declarative copilot agent."""
    return CopilotAgent(tools=build_tools(), system_prompt=system_prompt())
