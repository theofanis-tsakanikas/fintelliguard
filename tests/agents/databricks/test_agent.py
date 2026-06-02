"""Agent definition: the three tools are registered with names/descriptions/schemas."""

from __future__ import annotations

import pytest

from agents.databricks.agent import (
    GET_FRAUD_SCORE,
    QUERY_LAKEHOUSE,
    SEARCH_SIMILAR_CASES,
    build_agent,
)


def test_three_tools_registered_with_expected_names():
    agent = build_agent()
    assert agent.tool_names() == [QUERY_LAKEHOUSE, SEARCH_SIMILAR_CASES, GET_FRAUD_SCORE]


def test_each_tool_has_a_substantive_description_and_schema():
    agent = build_agent()
    for tool in agent.tools:
        assert len(tool.description) > 40  # descriptions are first-class
        assert tool.input_schema["type"] == "object"
        assert tool.input_schema["required"]


def test_tool_input_schemas_match_contracts():
    agent = build_agent()
    assert agent.get_tool(QUERY_LAKEHOUSE).input_schema["required"] == ["question"]
    assert agent.get_tool(SEARCH_SIMILAR_CASES).input_schema["required"] == ["query"]
    assert agent.get_tool(GET_FRAUD_SCORE).input_schema["required"] == [
        "transaction_id",
        "card_hash",
    ]


def test_descriptions_distinguish_routing():
    agent = build_agent()
    assert "exact" in agent.get_tool(QUERY_LAKEHOUSE).description.lower()
    assert "similar" in agent.get_tool(SEARCH_SIMILAR_CASES).description.lower()
    assert "why" in agent.get_tool(GET_FRAUD_SCORE).description.lower()


def test_unknown_tool_raises():
    with pytest.raises(KeyError, match="unknown tool"):
        build_agent().get_tool("nope")


def test_system_prompt_loaded():
    agent = build_agent()
    assert "routing" in agent.system_prompt.lower()
    assert len(agent.system_prompt) > 200
