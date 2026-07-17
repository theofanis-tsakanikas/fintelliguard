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


def build_checkpointer(config: HealingConfig):
    """Durable state for the healing thread. ON by default — it is load-bearing.

    Without it `retry_counts` resets on every cycle, so `attempts` is always 0, always
    `< max_pipeline_retries`, and `restart_pipeline` fires forever: the escalation branch
    is unreachable in production and a broken pipeline is restarted indefinitely with
    nobody paged. The tests only avoided this by threading state by hand
    (`run_self_healing(graph, first)`) — production had no mechanism to do that, so the
    test was quietly documenting the bug rather than covering it.

    It is also the precondition for the p99 confirmation window: `classify` is stateless,
    so debouncing needs `signal_history` to survive between cycles. The durability gap and
    the debounce gap were the same gap.

    Fails loud rather than falling back to an in-memory saver: a silent non-durable
    fallback would restore exactly the bug this exists to fix, while looking fixed.
    """
    if not config.enable_checkpointing:
        return None
    try:
        from langgraph.checkpoint.memory import InMemorySaver
    except ImportError as exc:  # pragma: no cover - langgraph is a hard dependency
        raise RuntimeError(
            "checkpointing is enabled but no saver is available; refusing to run "
            "non-durable, because retry counts that reset make escalation unreachable"
        ) from exc
    # In-process for the local funnel. A deployed daemon points this at Postgres — the
    # store is a deployment decision, the durability is not.
    return InMemorySaver()


def healing_thread_config(config: HealingConfig) -> dict[str, Any]:
    """The thread this healing loop resumes. STABLE — that is the whole point.

    A checkpointer with a per-run unique thread_id is decorative: each cycle would start a
    fresh thread and the state would reset exactly as it did before. A crash-and-restart
    must reload the same thread.
    """
    return {"configurable": {"thread_id": config.healing_thread_id}}


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
    compiled = graph.compile(checkpointer=build_checkpointer(config))
    # The thread the caller must resume. Attached to the graph so a caller cannot hold a
    # checkpointed graph and forget to pass the thread — which would silently restore the
    # reset-every-cycle behaviour.
    compiled.healing_config = healing_thread_config(config) if config.enable_checkpointing else {}
    return compiled


def run_self_healing(graph, state: HealthState | None = None) -> HealthState:
    """Run ONE healing cycle on the durable thread, returning the final state.

    A scheduler calls this every few minutes with no arguments. That used to mean
    `initial_state()` every time: `retry_counts` reset, `attempts` was always 0, and a
    broken pipeline was restarted forever while the escalation branch never ran.

    With a checkpointer and a stable thread id, LangGraph reloads the previous cycle's
    state, so retry counts accumulate, escalation is reachable, and `signal_history` is
    there for the confirmation window. Passing `state` explicitly still works and is what
    the tests do when they want to control the starting point.
    """
    thread = getattr(graph, "healing_config", {})
    if state is not None:
        return graph.invoke(state, thread)
    if not thread:
        return graph.invoke(initial_state(), thread)

    # Seed the defaults ONCE, on the thread's first cycle. Passing `initial_state()` on
    # every cycle would hand LangGraph `retry_counts={}` as an update and reset the counter
    # each time — the original bug, reintroduced through the mechanism meant to fix it.
    existing = graph.get_state(thread).values
    return graph.invoke({} if existing else initial_state(), thread)
