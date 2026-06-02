"""Assemble the self-healing LangGraph: collect -> supervisor -> (medic | end).

LangSmith tracing is configurable and OFF by default (no live calls in tests). Monitors
and remediation actions are injected, so the whole graph runs locally with mocks.
"""

from __future__ import annotations

import os
from typing import Any

from langgraph.graph import END, START, StateGraph

from agents.langgraph.config import HealingConfig
from agents.langgraph.medic import RemediationActions, medic_node
from agents.langgraph.state import HealthState, initial_state
from agents.langgraph.supervisor import (
    ROUTE_END,
    ROUTE_MEDIC,
    route_after_supervisor,
    supervisor_node,
)


def configure_tracing(config: HealingConfig) -> None:
    """Toggle LangSmith tracing via env. Never sets credentials — off means no live calls."""
    if config.enable_langsmith_tracing:
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_PROJECT"] = config.langsmith_project
    else:
        os.environ.pop("LANGCHAIN_TRACING_V2", None)


def build_self_healing_graph(monitors: Any, actions: RemediationActions, config: HealingConfig):
    """Compile the Supervisor + Medic graph with injected monitors/actions."""
    configure_tracing(config)

    def collect(state: HealthState) -> HealthState:
        return {"signals": monitors.collect()}

    def supervisor(state: HealthState) -> HealthState:
        return supervisor_node(state, config)

    def medic(state: HealthState) -> HealthState:
        return medic_node(state, actions, config)

    graph = StateGraph(HealthState)
    graph.add_node("collect", collect)
    graph.add_node("supervisor", supervisor)
    graph.add_node("medic", medic)

    graph.add_edge(START, "collect")
    graph.add_edge("collect", "supervisor")
    graph.add_conditional_edges(
        "supervisor", route_after_supervisor, {ROUTE_MEDIC: "medic", ROUTE_END: END}
    )
    graph.add_edge("medic", END)
    return graph.compile()


def run_self_healing(graph, state: HealthState | None = None) -> HealthState:
    """Run one healing cycle, returning the final state."""
    return graph.invoke(state or initial_state())
