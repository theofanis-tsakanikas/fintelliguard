"""DLT views and tables name themselves by different rules, and DLT enforces the difference.

A published `@dlt.table` is created in the catalog and MUST carry its medallion schema
(`bronze.`, `silver.`, `gold.`) per CLAUDE.md. A `@dlt.view` is session-scoped, never
published, and DLT rejects a schema-qualified name on it:

    AnalysisException: View with multipart name 'gold.txn_features_realtime_gated'
    is not supported.

All four `_gated` views shipped with a prefix and it surfaced only on the pipeline's first
real run — the local tests register the decorators through a stub that accepts any name, so
the one rule DLT actually enforces was the one rule nothing checked.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_PIPELINES = Path(__file__).resolve().parents[2] / "pipelines"
_MEDALLION = ("bronze.", "silver.", "gold.")


def _decorated_names(source: str) -> list[tuple[str, str, int]]:
    """(decorator, name-literal, lineno) for every @dlt.view / @dlt.table in the file."""
    out = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                continue
            if dec.func.attr not in ("view", "table"):
                continue
            for kw in dec.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    out.append((dec.func.attr, kw.value.value, dec.lineno))
    return out


_FILES = sorted(_PIPELINES.glob("*/*_pipeline.py"))


@pytest.mark.parametrize("path", _FILES, ids=lambda p: p.name)
def test_no_dlt_view_carries_a_schema_prefix(path: Path):
    for kind, name, lineno in _decorated_names(path.read_text("utf-8")):
        if kind == "view":
            assert not name.startswith(_MEDALLION), (
                f"{path.name}:{lineno} @dlt.view is named '{name}' — a view is session-scoped "
                "and DLT refuses a multipart name on it (AnalysisException at pipeline start). "
                "Drop the schema prefix; only published tables carry it."
            )


@pytest.mark.parametrize("path", _FILES, ids=lambda p: p.name)
def test_every_dlt_table_keeps_its_medallion_prefix(path: Path):
    """The other half: dropping the prefix from a published TABLE would break the convention
    and scatter tables into the pipeline's default schema."""
    for kind, name, lineno in _decorated_names(path.read_text("utf-8")):
        if kind == "table":
            assert name.startswith(_MEDALLION), (
                f"{path.name}:{lineno} @dlt.table '{name}' has no medallion schema prefix"
            )
