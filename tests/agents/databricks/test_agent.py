"""The agent declaration, checked AGAINST its implementations — not against itself.

`test_tool_input_schemas_match_contracts` used to re-assert the literals in `agent.py`:
it "matched" the declared schema to nothing, and so it passed while `get_fraud_score`'s
declared schema (`transaction_id`, `card_hash`) contradicted its implementation
(`get_fraud_score(features)`). A test named for a contract that checks no contract is worse
than no test — it reads as coverage. It now binds each declared tool to the callable that
implements it and checks the declared `required` fields ARE that callable's parameters.
"""

from __future__ import annotations

import inspect

import pytest

from agents.databricks.agent import (
    GET_FRAUD_SCORE,
    QUERY_LAKEHOUSE,
    SEARCH_SIMILAR_CASES,
    build_agent,
)
from agents.databricks.tools.get_fraud_score import FraudScoreTool
from agents.databricks.tools.search_similar_cases import SimilarCaseSearch


def test_three_tools_registered_with_expected_names():
    agent = build_agent()
    assert agent.tool_names() == [QUERY_LAKEHOUSE, SEARCH_SIMILAR_CASES, GET_FRAUD_SCORE]


def test_each_tool_declares_an_object_schema_with_required_fields():
    agent = build_agent()
    for tool in agent.tools:
        assert tool.input_schema["type"] == "object"
        assert tool.input_schema["required"], f"{tool.name} declares no required input"


def test_the_declared_schema_matches_the_implementation_that_serves_it():
    """The declared `required` fields must be the implementation's actual parameters.

    This is the check the old `test_tool_input_schemas_match_contracts` pretended to be. It
    caught nothing because it compared `agent.py` to `agent.py`; `get_fraud_score` declared
    `{transaction_id, card_hash}` while `FraudScoreTool.get_fraud_score(features)` took a
    feature vector, and the two disagreed in `main`.
    """
    agent = build_agent()

    # Only the tools with a LOCAL implementation are bound here. `query_lakehouse` is Genie
    # NL->SQL in production — a Databricks-hosted tool, not a Python method — so there is
    # nothing local to bind; `LakehouseTools` provides fixed helpers Genie's generated SQL
    # would call, not the routed tool. That deferral is asserted in
    # `test_query_lakehouse_is_genie_not_a_local_method`.
    implementations = {
        GET_FRAUD_SCORE: FraudScoreTool.get_fraud_score,
        SEARCH_SIMILAR_CASES: SimilarCaseSearch.search,
    }

    for name, method in implementations.items():
        declared = set(agent.get_tool(name).input_schema["required"])
        params = set(inspect.signature(method).parameters) - {"self"}
        assert declared <= params, (
            f"{name} declares required inputs {sorted(declared)} that its implementation "
            f"{method.__qualname__}{inspect.signature(method)} does not accept — the tool "
            "the LLM is told to call cannot be called with what it is told to send"
        )


def test_get_fraud_score_takes_ids_not_a_feature_vector():
    """The specific contradiction that shipped, pinned.

    The copilot holds a transaction id, never a feature vector. A tool declared as taking
    ids and implemented as taking features has no path from what the LLM sends to what the
    code runs.
    """
    params = set(inspect.signature(FraudScoreTool.get_fraud_score).parameters) - {"self"}
    assert params == {"transaction_id", "card_hash"}, (
        f"FraudScoreTool.get_fraud_score takes {sorted(params)} — the copilot cannot supply "
        "a feature vector, only the ids the Feature Store resolves"
    )


def test_query_lakehouse_is_genie_not_a_local_method():
    """Its declared input is a free-form question, because in production it IS Genie NL->SQL.

    `agents/databricks/tools/query_lakehouse.py` provides `LakehouseTools` — three fixed
    helpers (merchant_fraud_history, card_transaction_summary, device_usage) that Genie's
    generated SQL would call. None of them is the declared free-form `query_lakehouse
    (question)` tool, and pretending one was would be inventing a binding that does not
    exist. Stated here so the deferral is a documented fact, not a silent gap.
    """
    schema = build_agent().get_tool(QUERY_LAKEHOUSE).input_schema
    assert schema["required"] == ["question"], (
        "query_lakehouse routes to Genie NL->SQL; its input is a natural-language question, "
        "not the fixed parameters of a local helper"
    )


def test_unknown_tool_raises():
    with pytest.raises(KeyError, match="unknown tool"):
        build_agent().get_tool("nope")


def test_system_prompt_is_loaded_from_the_versioned_instructions():
    agent = build_agent()
    # A weak check, and named as one: it confirms the file is wired, not that it is good.
    assert agent.system_prompt.strip(), "the routing system prompt is empty"
