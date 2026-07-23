"""Every third-party import must be declared in `pyproject.toml`.

`agents/bedrock/kb/ingest.py` landed importing `boto3` without declaring it. The full suite
passed locally — boto3 happened to be in the venv as a transitive dependency of something
else — and CI's clean install failed collecting the tests (run 29710998146). A dependency
satisfied by accident is not declared, it is inherited, and the only machine that proves the
difference is the one that installs from scratch.

This walks the repo's own packages, collects the top-level modules they import, drops the
standard library and first-party packages, and requires the rest to be named in
`pyproject.toml`. It is a cheap, complete check: adding an undeclared import cannot pass it,
and it does not depend on what happens to be installed while it runs — the failure mode of
the thing it guards.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

# Packages this repo ships. An import of one of these is not a dependency.
_FIRST_PARTY = {"agents", "ml", "pipelines", "simulator", "scripts", "tests", "dashboards"}

# Import name -> distribution name, where they differ. Kept explicit rather than resolved
# from the installed environment, because resolving it there would make this test pass or
# fail based on what is installed — which is the very thing it exists to stop trusting.
_DISTRIBUTION = {
    "yaml": "pyyaml",
    "sklearn": "scikit-learn",
    "hcl2": "python-hcl2",
    "mlflow": "mlflow-skinny",
    "confluent_kafka": "confluent-kafka",
    "prometheus_client": "prometheus-client",
    "dateutil": "python-dateutil",
    "aws_msk_iam_sasl_signer": "aws-msk-iam-sasl-signer-python",
    # Ships inside boto3; there is no separate thing to declare.
    "botocore": "boto3",
}

# Modules the EXECUTION RUNTIME provides, which cannot be pip-installed into this project.
#
# This is the one exemption, and it is narrow on purpose — an exemption list is how a check
# like this rots. Each entry is a module that only exists inside the environment its code
# runs in, so declaring it would be a lie that also breaks `pip install`:
#
#   dlt        — Databricks Delta Live Tables. Injected by the DLT runtime; there is no
#                public package. `pipelines/*` import it and are never imported by tests.
#   databricks — the Databricks SDK, present on every cluster. Imported INSIDE a function in
#                `infra/bundles/scripts/genie_space.py` precisely so that importing the
#                module does not require it.
#
# Nothing here may be imported at module scope by code the test suite collects; if that ever
# happens, CI fails to collect and this list is not what saved it.
_RUNTIME_PROVIDED = {"dlt", "databricks"}

_SOURCE_DIRS = ("agents", "ml", "pipelines", "simulator", "scripts", "tests", "infra")


def _declared() -> set[str]:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data.get("project", {})
    specs = list(project.get("dependencies", []))
    for group in project.get("optional-dependencies", {}).values():
        specs.extend(group)
    # "ruff==0.15.15" / "boto3" / "pandas>=2" -> the bare name, lowercased.
    return {
        re.split(r"[<>=!~\[;\s]", spec, maxsplit=1)[0].strip().lower() for spec in specs if spec
    }


def _imports_in(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:  # not ours to police here
        return set()
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # `from . import x` has no module; relative imports are first-party by definition.
            if node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return found


def _local_modules() -> set[str]:
    """Top-level module names this repo ships OUTSIDE any package.

    `agents/bedrock/lambda/` has no `__init__.py` on purpose: AWS Lambda puts the handler's
    directory on `sys.path`, so `handler.py` does `from clients import ...`. The tests import
    them the same way, via a conftest that adds that directory. Those names are first-party
    and resolving them by looking for a sibling file next to the IMPORTER missed it — the
    importer is a test three directories away.
    """
    names = set()
    for directory in _SOURCE_DIRS:
        for path in (_ROOT / directory).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            if not (path.parent / "__init__.py").exists():
                names.add(path.stem)
    return names


def _third_party_imports() -> dict[str, list[str]]:
    """Top-level third-party module -> the files importing it."""
    by_module: dict[str, list[str]] = {}
    for directory in _SOURCE_DIRS:
        for path in (_ROOT / directory).rglob("*.py"):
            if "__pycache__" in path.parts or ".terraform" in path.parts:
                continue
            for module in _imports_in(path):
                if module in sys.stdlib_module_names or module in _FIRST_PARTY:
                    continue
                if module in _RUNTIME_PROVIDED:
                    continue
                if module in _local_modules():
                    continue
                by_module.setdefault(module, []).append(str(path.relative_to(_ROOT)))
    return by_module


def test_the_scan_finds_imports_at_all():
    """Guards the walk: if it silently found nothing, the test below would pass vacuously."""
    found = _third_party_imports()
    assert len(found) >= 5, f"the import scan found only {sorted(found)} — the walk is broken"


@pytest.mark.parametrize(
    ("module", "files"),
    sorted(_third_party_imports().items()),
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_every_third_party_import_is_declared(module: str, files: list[str]):
    distribution = _DISTRIBUTION.get(module, module).lower()
    declared = _declared()
    assert distribution in declared, (
        f"'{module}' is imported by {', '.join(sorted(files)[:3])} but no dependency named "
        f"'{distribution}' is declared in pyproject.toml. It may be installed locally as a "
        "transitive dependency of something else — CI installs from scratch and will fail. "
        f"Add it, or map the import name in {__name__}._DISTRIBUTION."
    )
