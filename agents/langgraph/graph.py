"""Assemble the self-healing LangGraph: collect -> supervisor -> (medic | end).

LangSmith tracing is configurable and OFF by default (no live calls in tests). Monitors
and remediation actions are injected, so the whole graph runs locally with mocks.
"""

from __future__ import annotations

import logging
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

logger = logging.getLogger("fintelliguard.self_healing")


def configure_tracing(config: HealingConfig) -> None:
    """Toggle LangSmith tracing via env. Never sets credentials — off means no live calls."""
    if config.enable_langsmith_tracing:
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_PROJECT"] = config.langsmith_project
    else:
        os.environ.pop("LANGCHAIN_TRACING_V2", None)


def build_checkpointer(config: HealingConfig):
    """Durable state for the healing thread. Load-bearing, not a nice-to-have.

    Without ANY checkpointer, `retry_counts` resets every cycle: `attempts` is always 0,
    always below `max_pipeline_retries`, `restart_pipeline` fires forever, the escalation
    branch is unreachable and nobody is paged. The tests only avoided that by threading
    state by hand (`run_self_healing(graph, first)`) — production had no mechanism to do
    that, so the test was documenting the bug rather than covering it. It is also the
    precondition for the p99 confirmation window: `classify` is stateless, so debouncing
    needs `signal_history` to survive between cycles. The durability gap and the debounce
    gap were the same gap.

    Two levels, and the difference matters:

    * `checkpoint_db` set -> SQLite. State survives a process restart, so the retry bound
      and `max_total_actions` are per-THREAD, which is what they claim to be.
    * `checkpoint_db` unset -> in-memory. State survives across cycles WITHIN one process
      and dies with it, so a crash hands the agent a fresh action budget. Adequate for the
      local funnel and for tests; not adequate for a daemon.

    This function's docstring used to say it "fails loud rather than falling back to an
    in-memory saver" — and the next line returned `InMemorySaver()`. The fallback is still
    there because it is the right default for a test suite, but it is now named, warned
    about, and distinguished from the thing it is not.
    """
    if not config.enable_checkpointing:
        return None

    if config.checkpoint_db:
        try:
            import sqlite3

            from langgraph.checkpoint.sqlite import SqliteSaver
        except ImportError as exc:  # pragma: no cover - the dep is declared
            # Loud. A silent fall-through to in-memory would restore exactly the bug this
            # exists to fix, while looking fixed — which is this project's whole disease.
            raise RuntimeError(
                f"checkpoint_db={config.checkpoint_db!r} asks for durable state and "
                "langgraph-checkpoint-sqlite is not installed. Refusing to run non-durable: "
                "retry counts that reset make escalation unreachable."
            ) from exc
        # A raw connection, NOT `from_conn_string` — that is a @contextmanager, and
        # compiling with it yields a graph whose checkpointer has no `get_tuple`/`put` and
        # dies on first invoke.
        return SqliteSaver(sqlite3.connect(config.checkpoint_db, check_same_thread=False))

    from langgraph.checkpoint.memory import InMemorySaver

    logger.warning(
        "self-healing state is IN-MEMORY: retry counts and the action budget survive "
        "cycles but not a restart, so a crash hands the agent a fresh budget. Set "
        "HealingConfig.checkpoint_db for a daemon."
    )
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
