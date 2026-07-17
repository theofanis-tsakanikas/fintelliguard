"""End-to-end self-healing graph runs (real LangGraph, mocked signals/actions)."""

from __future__ import annotations

import os

from agents.langgraph.config import HealingConfig
from agents.langgraph.graph import (
    build_self_healing_graph,
    configure_tracing,
    healing_thread_config,
    run_self_healing,
)
from agents.langgraph.state import (
    OUTCOME_ESCALATED,
    OUTCOME_HEALTHY,
    OUTCOME_REMEDIATED,
    OUTCOME_UNKNOWN,
    initial_state,
)

from .conftest import (
    StubMonitors,
    confirmed_p99_history,
    degraded_endpoint_signals,
    healthy_signals,
    high_lag_signals,
    pipeline_failed_signals,
)

_CONFIG = HealingConfig()


def _graph(signals, actions):
    return build_self_healing_graph(StubMonitors(signals), actions, _CONFIG)


def test_healthy_run_takes_no_action(actions, mlflow_client):
    final = run_self_healing(_graph(healthy_signals(), actions))
    assert final["incident"] is None
    assert final["actions_taken"] == []
    assert mlflow_client.transitions == []
    assert final["decisions"][-1] == "supervisor: healthy"


def _confirmed_state():
    """A state whose p99 breach has already persisted long enough to act on."""
    state = initial_state()
    state["signal_history"] = confirmed_p99_history()
    return state


def test_a_confirmed_endpoint_breach_rolls_back(actions, mlflow_client):
    final = run_self_healing(_graph(degraded_endpoint_signals(), actions), _confirmed_state())
    assert final["incident"]["failure_class"] == "endpoint_latency"
    assert final["actions_taken"][-1]["action"] == "rollback_model"
    assert len(mlflow_client.transitions) == 1
    assert final["outcome"] == OUTCOME_REMEDIATED


def test_a_single_endpoint_spike_changes_nothing(actions, mlflow_client):
    """The debounce, end to end through the real graph.

    One reading above 200ms used to archive the Production model and promote another.
    """
    final = run_self_healing(_graph(degraded_endpoint_signals(), actions))
    assert final["incident"] is None
    assert final["actions_taken"] == []
    assert mlflow_client.transitions == []  # nothing touched the registry
    assert final["outcome"] == OUTCOME_HEALTHY


def test_high_lag_scales(actions):
    final = run_self_healing(_graph(high_lag_signals(), actions))
    assert final["actions_taken"][-1]["action"] == "scale_consumers"
    assert actions.scale_consumers.calls == [("txn.raw", 2)]


def test_pipeline_failure_retries_then_escalates_across_cycles(actions):
    graph = _graph(pipeline_failed_signals(), actions)
    first = run_self_healing(graph)
    assert first["actions_taken"][-1]["action"] == "retry_pipeline"
    assert actions.restart_pipeline.calls == [("pl-1",)]

    # Pipeline still failing on the next cycle (carry state) -> escalate.
    second = run_self_healing(graph, first)
    assert second["actions_taken"][-1]["action"] == "escalate_pipeline"
    assert actions.escalate.calls


def test_same_incident_is_not_remediated_twice(actions, mlflow_client):
    graph = _graph(degraded_endpoint_signals(), actions)
    first = run_self_healing(graph, _confirmed_state())
    assert len(mlflow_client.transitions) == 1

    second = run_self_healing(graph, first)  # same incident, carried state
    assert len(mlflow_client.transitions) == 1  # NOT rolled back again
    assert "skip" in second["decisions"][-1]


def test_tracing_is_off_by_default_and_toggles():
    os.environ.pop("LANGCHAIN_TRACING_V2", None)
    configure_tracing(HealingConfig())
    assert os.environ.get("LANGCHAIN_TRACING_V2") != "true"

    configure_tracing(HealingConfig(enable_langsmith_tracing=True))
    assert os.environ["LANGCHAIN_TRACING_V2"] == "true"
    configure_tracing(HealingConfig())  # reset off
    assert os.environ.get("LANGCHAIN_TRACING_V2") != "true"


# --------------------------------------------------------------------------- #
# Safety bounds — what the agent is NOT allowed to do
# --------------------------------------------------------------------------- #


def test_the_agent_stops_acting_once_its_budget_is_spent(actions):
    """The blast-radius cap.

    `retry_counts[fingerprint]` bounded ONE incident. Nothing bounded the total, so N
    distinct failing pipelines each received their own retry budget and the agent could act
    without limit across them. Past the ceiling it escalates instead.
    """
    config = HealingConfig(max_total_actions=2)
    graph = build_self_healing_graph(StubMonitors(high_lag_signals()), actions, config)

    state = initial_state()
    state["total_actions"] = 2  # budget already spent
    final = run_self_healing(graph, state)

    assert final["actions_taken"] == []  # refused to act
    assert final["outcome"] == OUTCOME_ESCALATED
    assert "budget" in final["decisions"][-1]
    assert actions.escalate.calls, "a human was not paged"


def test_a_healthy_run_is_not_reported_as_a_remediation(actions):
    """`outcome` distinguishes the three states a caller used to have to guess at.

    There was no outcome field: a scheduler read `actions_taken` and inferred. "Healthy,
    nothing to do", "acted and fixed it" and "gave up, page someone" are very different
    things for a payment system, and they were indistinguishable.
    """
    final = run_self_healing(_graph(healthy_signals(), actions))
    assert final["outcome"] == OUTCOME_HEALTHY
    assert final["actions_taken"] == []


def test_reaching_the_end_of_the_graph_is_not_success_by_itself():
    """The default outcome is not a success value."""
    assert initial_state()["outcome"] == OUTCOME_UNKNOWN


# --------------------------------------------------------------------------- #
# Durability — the gap the tests were hiding
# --------------------------------------------------------------------------- #


def test_a_scheduler_calling_with_no_arguments_still_escalates(actions):
    """The bug the old test was quietly documenting.

    `test_pipeline_failure_retries_then_escalates_across_cycles` threads state by hand:

        first = run_self_healing(graph)
        second = run_self_healing(graph, first)   # <- production never did this

    It passed BECAUSE it did the thing production had no mechanism to do. A scheduler calls
    `run_self_healing(graph)` every few minutes with no arguments; that defaulted to
    `initial_state()`, so `retry_counts` reset, `attempts` was always 0, always below the
    bound, and `restart_pipeline` fired forever. The escalation branch was unreachable in
    production and a broken pipeline was restarted indefinitely with nobody paged.

    This calls it the way a scheduler does — no state, every time — and requires that a
    human is eventually paged.
    """
    config = HealingConfig(max_pipeline_retries=1, healing_thread_id="test-durability")
    graph = build_self_healing_graph(StubMonitors(pipeline_failed_signals()), actions, config)

    first = run_self_healing(graph)
    assert first["actions_taken"][-1]["action"] == "retry_pipeline"

    second = run_self_healing(graph)  # no state passed — exactly what a scheduler does
    assert second["actions_taken"][-1]["action"] == "escalate_pipeline", (
        "the retry counter reset between cycles, so escalation is unreachable and the "
        "pipeline is restarted forever"
    )
    assert second["outcome"] == OUTCOME_ESCALATED
    assert actions.escalate.calls, "no human was ever paged"


def test_the_confirmation_window_survives_between_cycles(actions, mlflow_client):
    """Durability is the precondition for debouncing, not a separate feature.

    `classify` is stateless, so the p99 window needs `signal_history` to persist. Without a
    checkpointer there is no history, and without history every breach looks like its first.
    """
    config = HealingConfig(healing_thread_id="test-window")
    graph = build_self_healing_graph(StubMonitors(degraded_endpoint_signals()), actions, config)

    # Assert AFTER EACH cycle, not after all of them: collecting the runs first and
    # checking at the end would let the confirming cycle fire before the assertion looks.
    for cycle in range(1, config.p99_confirmations_required):
        state = run_self_healing(graph)
        assert state["incident"] is None, f"acted on cycle {cycle}, before the breach confirmed"
        assert mlflow_client.transitions == [], f"touched the registry on cycle {cycle}"

    final = run_self_healing(graph)
    assert final["incident"]["failure_class"] == "endpoint_latency"
    assert len(mlflow_client.transitions) == 1


def test_a_stable_thread_id_is_used():
    """A per-run thread id makes a checkpointer decorative — each cycle starts fresh."""
    first = healing_thread_config(HealingConfig())
    second = healing_thread_config(HealingConfig())
    assert first == second
    assert first["configurable"]["thread_id"]
