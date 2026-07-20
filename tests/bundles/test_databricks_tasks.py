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

from tests.cicd.test_workflows import _load

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


# --------------------------------------------------------------------------- #
# Ordering DAB cannot express, and the deploy therefore must
# --------------------------------------------------------------------------- #


def _yaml(path: str) -> dict:
    return yaml.safe_load((_ROOT / path).read_text(encoding="utf-8")) or {}


def test_the_vector_endpoint_is_created_before_the_index_that_needs_it():
    """An index cannot be built on an endpoint that is still provisioning.

    Both were declared in the main bundle, and DAB deploys a bundle's resources in one pass
    with no ordering — so the index was requested against an endpoint that existed only as an
    accepted API call:

        NOT_FOUND: AI Search endpoint fintelliguard-cases not found.

    That reads like a naming or permissions fault and is neither. The endpoint belongs with
    the seed job, in the bundle whose entire purpose is "things the main bundle needs to
    already exist".
    """
    main = _yaml("infra/bundles/resources/vector_search.yml")["resources"]
    prereq = _yaml("infra/bundles/prereq/databricks.yml")["resources"]

    assert "vector_search_indexes" in main, "the case index left the main bundle"
    assert "vector_search_endpoints" not in main, (
        "the Vector Search endpoint is declared beside the index again — DAB gives them no "
        "ordering, so the index races an endpoint that takes minutes to provision"
    )
    assert "vector_search_endpoints" in prereq, (
        "nothing creates the Vector Search endpoint before the main bundle needs it"
    )


def test_the_deploy_waits_for_the_endpoint_to_be_online():
    """Creating an endpoint is not the same as having one.

    `bundle deploy` returns when Databricks ACCEPTS the request; the endpoint is PROVISIONING
    for minutes afterwards. Without an explicit wait the ordering above buys nothing.
    """
    steps = _load("deploy")["jobs"]["apply"]["steps"]
    names = [s.get("name", "") for s in steps]
    wait = next((i for i, n in enumerate(names) if "ONLINE" in n), None)
    assert wait is not None, "the deploy never waits for the Vector Search endpoint"

    main_bundle = next(i for i, n in enumerate(names) if n.startswith("4b"))
    assert wait < main_bundle, "the wait happens after the bundle that needs the endpoint"

    script = steps[wait]["run"]
    assert "ONLINE" in script, "the wait does not check for the ONLINE state"
    # A wait that only breaks on success hangs for the full timeout on a dead endpoint, and
    # the log gives no reason — the failure states have to end it too.
    assert "FAILED" in script, (
        "the wait does not stop on a terminal failure state, so a dead endpoint burns the "
        "whole timeout and reports nothing useful"
    )


# --------------------------------------------------------------------------- #
# The ML chain: pipelines -> data -> training -> registered model -> serving
# --------------------------------------------------------------------------- #


def test_the_ml_chain_runs_in_dependency_order():
    """Each link needs the one before it, and DAB deploys a bundle in ONE pass.

    The serving endpoints used to sit in the same bundle as the DLT pipeline whose output
    trains the model they serve, so a clean estate could never deploy: the bundle failed with
    "Registered model 'fintelliguard.ml.fraud_scorer' does not exist" every time.
    """
    steps = _load("deploy")["jobs"]["apply"]["steps"]
    names = [s.get("name", "") for s in steps]

    def index(prefix: str) -> int:
        found = next((i for i, n in enumerate(names) if n.startswith(prefix)), None)
        assert found is not None, f"the deploy has no step '{prefix}'"
        return found

    order = [index(p) for p in ("4b)", "5)", "6)", "7)", "8)")]
    assert order == sorted(order), (
        f"the ML chain is out of order: {[names[i] for i in order]}. It must be "
        "bundle -> data -> pipeline -> training -> serving"
    )


def test_serving_is_pinned_to_the_promoted_version_not_the_latest():
    """The one assertion that keeps the promotion gate real.

    `evaluate_promotion` rejects a model below AUC-ROC 0.92 or fraud precision 0.85, and a
    rejected model is still REGISTERED — it just never takes the `production` alias. Deploying
    "the latest version" would therefore serve precisely the model the gate refused, while the
    gate's own logs still read REJECT. The endpoint must be pinned by alias.
    """
    steps = _load("deploy")["jobs"]["apply"]["steps"]
    serving = next(s for s in steps if s.get("name", "").startswith("8)"))
    script = serving["run"]

    assert "get-by-alias" in script and "production" in script, (
        "the serving deploy does not resolve the version from the `production` alias, so it "
        "can serve a model the promotion gate rejected"
    )
    assert "latest" not in script.lower(), (
        "the serving deploy mentions 'latest' — the newest version is exactly what the gate "
        "may have rejected"
    )
    # No fallback default: a missing alias must stop the deploy, not serve version 1.
    serving_bundle = _yaml("infra/bundles/serving/databricks.yml")
    assert "default" not in serving_bundle["variables"]["fraud_model_version"], (
        "fraud_model_version has a default again — it would serve whatever was registered "
        "first whenever the alias lookup is skipped or fails"
    )


def test_the_agent_endpoint_is_not_deployed_while_nothing_registers_its_model():
    """`agents/databricks/` contains no mlflow.log_model, so the copilot model cannot exist.

    Including its endpoint would reproduce the exact failure the serving split removed.
    """
    include = _yaml("infra/bundles/serving/databricks.yml")["include"]
    assert "./agent_serving.yml" not in include, (
        "the copilot endpoint is included, but nothing logs or registers "
        "fintelliguard.ml.copilot_agent — the deploy will fail on it"
    )


def test_the_case_index_source_table_enables_change_data_feed():
    """A DELTA_SYNC index syncs by READING the source table's change data feed.

    Without it Databricks refuses to build the index at all:

        Source table fintelliguard.gold.resolved_cases is not a valid Vector Search source.
        Please retry after enabling change data feed (delta.enableChangeDataFeed = true).

    The property belongs to the TABLE, so it belongs to the job that creates the table — and
    it must be ALTERed rather than passed as a writer option, which only takes effect when
    the table is first created and would leave a pre-existing table silently unchanged.
    """
    index_config = _yaml("infra/bundles/resources/vector_search.yml")["resources"]
    index = index_config["vector_search_indexes"]["similar_cases"]
    assert index["index_type"] == "DELTA_SYNC", (
        "the index is no longer DELTA_SYNC — this test guards a requirement of that type"
    )

    seed = (_ROOT / "infra/bundles/prereq/seed_resolved_cases.py").read_text("utf-8")
    code = "\n".join(line for line in seed.splitlines() if not line.lstrip().startswith("#"))
    assert "delta.enableChangeDataFeed" in code and "ALTER TABLE" in code, (
        "the seed job does not enable change data feed on gold.resolved_cases, so the "
        "DELTA_SYNC index cannot be created against it"
    )


def test_the_embedding_endpoint_is_verified_before_the_bundle_needs_it():
    """Which foundation models exist is REGIONAL, and the index reports a missing one as its
    own failure:

        cannot create resources.vector_search_indexes.similar_cases:
        Model serving endpoint databricks-bge-large-en not found. (404)

    That was the fourth distinct error from this one bundle step, each hiding the next. The
    check reads the name FROM the bundle — a preflight that keeps its own copy of the value
    passes while the bundle asks for something else.
    """
    steps = _load("deploy")["jobs"]["apply"]["steps"]
    names = [s.get("name", "") for s in steps]
    check = next((i for i, n in enumerate(names) if n.startswith("4a-iii")), None)
    assert check is not None, "nothing verifies the embedding endpoint before the bundle"
    assert check < next(i for i, n in enumerate(names) if n.startswith("4b")), (
        "the embedding endpoint is verified after the bundle that needs it"
    )

    script = steps[check]["run"]
    assert "embedding_endpoint_name" in script, (
        "the preflight hardcodes an endpoint name instead of reading the bundle's, so the "
        "two can disagree and the check would still pass"
    )


def _dlt_sources() -> list[Path]:
    """Every file the DLT pipeline declares as a library, from the bundle definition."""
    config = _yaml("infra/bundles/resources/pipelines.yml")
    libraries = config["resources"]["pipelines"]["medallion"]["libraries"]
    base = _ROOT / "infra" / "bundles" / "resources"
    return [(base / entry["file"]["path"]).resolve() for entry in libraries if "file" in entry]


def test_the_bundle_declares_the_dlt_sources():
    """Guards the discovery, so the check below cannot pass by finding nothing."""
    sources = _dlt_sources()
    assert len(sources) >= 3, f"expected the medallion layers, found {sources}"
    for path in sources:
        assert path.is_file(), f"{path} is declared as a DLT library but does not exist"


@pytest.mark.parametrize("source", _dlt_sources(), ids=lambda p: p.name)
def test_a_dlt_source_uses_absolute_imports(source: Path):
    """DLT runs each source like a notebook cell — there is no parent package.

    All three pipelines opened with `from . import <layer>_transforms`, which is correct
    Python and cannot work here:

        ImportError: attempted relative import with no known parent package

    `resources/pipelines.yml` had predicted it in a comment and deferred it as a
    "deploy-phase refinement". It stayed invisible because the local tests import these
    modules AS a package, where the relative form resolves perfectly — the one context that
    could not reproduce the one thing that mattered.
    """
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level:
            raise AssertionError(
                f"{source.name}:{node.lineno} uses a relative import "
                f"({'.' * node.level}{node.module or ''}) — DLT executes this file with no "
                "parent package, so it raises ImportError at pipeline start"
            )
