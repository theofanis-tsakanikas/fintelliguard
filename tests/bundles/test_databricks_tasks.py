"""Entry-point rules for scripts Databricks runs as a job task.

A `spark_python_task` does NOT run as a standalone process. Databricks executes it inside a
notebook-like host, which changes two ordinary Python idioms into bugs — and both were
learned from a cluster that had already spent minutes doing the work correctly before
failing on the way out.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_BUNDLES = _ROOT / "infra" / "bundles"


def _task_scripts() -> list[Path]:
    """Every file a bundle declares as a `python_file`, resolved against its own bundle.

    Discovered from the bundle definitions rather than listed here, so a new job task is
    covered the moment it is declared instead of whenever someone remembers this file.
    """
    found = []
    for config in _BUNDLES.rglob("*.yml"):
        doc = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
        for node in _walk(doc):
            if isinstance(node, dict) and "python_file" in node:
                found.append((config.parent / node["python_file"]).resolve())
    return found


def _walk(node):
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def test_the_bundles_declare_at_least_one_task_script():
    """Guards the discovery itself: if the walk breaks, every test below vacuously passes."""
    assert _task_scripts(), "no python_file tasks found — the bundle walk is broken"


@pytest.mark.parametrize("script", _task_scripts(), ids=lambda p: p.name)
def test_a_task_script_never_calls_sys_exit(script: Path):
    """`sys.exit(0)` reports a SUCCESSFUL job as INTERNAL_ERROR.

    Inside Databricks' notebook-like host `SystemExit` is an exception that propagates, not a
    process terminator. `seed_resolved_cases.py` ended with `sys.exit(main())`, wrote the
    table exactly as intended, and then failed the deploy with

        SystemExit: 0
        Task seed failed with message: Workload failed
        Error: failed to reach TERMINATED or SKIPPED, got INTERNAL_ERROR

    eleven minutes into deploy run 29707464142. Returning normally is the success signal and
    an exception is the failure signal; an exit code is not a third channel, it is a wrong one.
    """
    tree = ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        name = (
            f"{target.value.id}.{target.attr}"
            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
            else getattr(target, "id", "")
        )
        assert name not in ("sys.exit", "exit", "quit"), (
            f"{script.name}:{node.lineno} calls {name}() — inside a Databricks task that "
            "raises SystemExit, which is reported as INTERNAL_ERROR even when the value is 0"
        )


@pytest.mark.parametrize("script", _task_scripts(), ids=lambda p: p.name)
def test_a_task_script_does_not_rely_on_dunder_file(script: Path):
    """Databricks EXECUTES the file rather than importing it, so `__file__` is undefined.

    The first version of the seed resolved its repo root from `__file__` and died on
    NameError after the cluster had already spun up — three minutes for a one-line fact.

    Read through the AST, not the text. The first version of THIS test grepped the source and
    failed the seed script for its own docstring explaining why `__file__` is not used —
    flagging a file for documenting the rule it obeys. That is the same "cannot tell a
    command from a comment about one" mistake the suite exists to catch.
    """
    tree = ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "__file__":
            raise AssertionError(
                f"{script.name}:{node.lineno} reads __file__, which is undefined in a "
                "Databricks task — resolve paths from sys.argv[0] or cwd instead"
            )
