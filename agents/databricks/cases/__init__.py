"""Synthetic seed for the Tier-3 resolved-cases index. See `seed.py` for why it exists
and why every row must declare itself synthetic."""

from __future__ import annotations

from agents.databricks.cases.seed import (
    CASE_ID_PREFIX,
    DISCLOSURE,
    PROVENANCE,
    ResolvedCase,
    build_seed_cases,
    outcome_mix,
    resolved_cases_schema,
)

__all__ = [
    "CASE_ID_PREFIX",
    "DISCLOSURE",
    "PROVENANCE",
    "ResolvedCase",
    "build_seed_cases",
    "outcome_mix",
    "resolved_cases_schema",
]
