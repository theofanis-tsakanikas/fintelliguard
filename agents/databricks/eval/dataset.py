"""Labeled evaluation dataset: analyst questions -> expected tool + answer shape.

A representative set of fraud-analyst questions used to gate changes to prompts / tool
descriptions. `expected_tool` is one of the agent's tool names; `answer_shape` describes
the structure a correct answer takes.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents.databricks.agent import GET_FRAUD_SCORE, QUERY_LAKEHOUSE, SEARCH_SIMILAR_CASES

# Answer-shape labels (what a correct answer looks like).
SHAPE_SCALAR_FACTS = "scalar_facts"
SHAPE_SIMILAR_CASES = "similar_cases"
SHAPE_SCORE_EXPLANATION = "score_explanation"


@dataclass(frozen=True)
class EvalCase:
    """One labeled evaluation example."""

    question: str
    expected_tool: str
    answer_shape: str


_CASES: tuple[EvalCase, ...] = (
    # query_lakehouse — precise, structured facts.
    EvalCase(
        "What is the historical fraud rate for merchant M00012?",
        QUERY_LAKEHOUSE,
        SHAPE_SCALAR_FACTS,
    ),
    EvalCase(
        "How many transactions has this card made in total?", QUERY_LAKEHOUSE, SHAPE_SCALAR_FACTS
    ),
    EvalCase(
        "How many distinct cards have used device D00045?", QUERY_LAKEHOUSE, SHAPE_SCALAR_FACTS
    ),
    EvalCase("What is the total amount spent on this card?", QUERY_LAKEHOUSE, SHAPE_SCALAR_FACTS),
    # search_similar_cases — semantic similarity.
    EvalCase("Show me past cases similar to this one.", SEARCH_SIMILAR_CASES, SHAPE_SIMILAR_CASES),
    EvalCase(
        "Have we seen fraud patterns like this before?", SEARCH_SIMILAR_CASES, SHAPE_SIMILAR_CASES
    ),
    EvalCase(
        "Find precedents resembling this transaction.", SEARCH_SIMILAR_CASES, SHAPE_SIMILAR_CASES
    ),
    # get_fraud_score — why flagged.
    EvalCase("Why was this transaction flagged?", GET_FRAUD_SCORE, SHAPE_SCORE_EXPLANATION),
    EvalCase(
        "What features drove the fraud score for this transaction?",
        GET_FRAUD_SCORE,
        SHAPE_SCORE_EXPLANATION,
    ),
    EvalCase("Explain the model's score for this case.", GET_FRAUD_SCORE, SHAPE_SCORE_EXPLANATION),
)


def eval_dataset() -> tuple[EvalCase, ...]:
    """The labeled evaluation cases."""
    return _CASES
