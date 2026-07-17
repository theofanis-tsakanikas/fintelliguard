"""Validate the GitHub Actions workflows: well-formed YAML + intended triggers/commands.

YAML 1.1 parses the `on:` key as the boolean True, so trigger config is read from either
key. We do NOT trigger any cloud workflow — this only inspects the files.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
_GATED = ("bootstrap", "deploy", "destroy")


def _load(name: str) -> dict:
    return yaml.safe_load((_WORKFLOWS / f"{name}.yml").read_text(encoding="utf-8"))


def _triggers(doc: dict) -> dict:
    # `on:` -> True under YAML 1.1; fall back to the string key.
    return doc.get(True, doc.get("on", {}))


def _text(name: str) -> str:
    return (_WORKFLOWS / f"{name}.yml").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["ci", *_GATED])
def test_workflow_is_well_formed(name):
    doc = _load(name)
    assert isinstance(doc, dict)
    assert doc.get("name")
    assert isinstance(doc.get("jobs"), dict) and doc["jobs"]


def test_ci_triggers_on_pr_and_push():
    triggers = _triggers(_load("ci"))
    assert "pull_request" in triggers
    assert "push" in triggers


@pytest.mark.parametrize("name", _GATED)
def test_gated_workflows_are_manual_only(name):
    triggers = _triggers(_load(name))
    # Manual dispatch ONLY — never auto-triggered on push/PR.
    assert set(triggers) == {"workflow_dispatch"}


def test_ci_reproduces_local_gates():
    text = _text("ci")
    for command in [
        "ruff check .",
        "ruff format --check .",
        "pytest -q",
        "terraform fmt -check -recursive",
        'terraform -chdir="${dir}" validate',
        "databricks bundle validate",
        'python-version: "3.11"',
        'java-version: "17"',
    ]:
        assert command in text, f"ci.yml missing: {command}"


def test_ci_enforces_the_responsible_ai_gates():
    """The gates CLAUDE.md calls CI-enforced must actually be steps in CI.

    This test existed and omitted exactly these three commands, so deleting the
    Responsible-AI step from `ci.yml` left the whole suite green — the gates were
    "CI-enforced" by assertion only. They are the ones that most need pinning.
    """
    text = _text("ci")
    for command in [
        # the guardrail red-team coverage gate
        "python -m agents.bedrock.guardrails.evaluate",
        # the generated governance docs must match the code
        "python -m ml.governance.generate --check",
        # and the gates themselves must be provably able to fail
        "python -m scripts.gate_proof",
    ]:
        assert command in text, f"ci.yml no longer runs the Responsible-AI gate: {command}"


def test_ci_validates_every_terraform_layer():
    text = _text("ci")
    for layer in [
        "infra/aws",
        "infra/aws/bootstrap",
        "infra/databricks",
        "agents/bedrock/terraform",
    ]:
        assert layer in text


def test_destroy_requires_typed_confirmation():
    text = _text("destroy")
    # The guard compares the typed confirmation to the environment name.
    assert "inputs.confirm" in text
    assert "inputs.environment" in text
    assert '"${{ inputs.confirm }}" != "${{ inputs.environment }}"' in text
    doc = _load("destroy")
    assert "confirm" in _triggers(doc)["workflow_dispatch"]["inputs"]


def test_deploy_has_environment_input_and_ordered_layers():
    doc = _load("deploy")
    assert "environment" in _triggers(doc)["workflow_dispatch"]["inputs"]
    text = _text("deploy")
    # Ordered apply across the four layers.
    for step in [
        "1) infra/aws",
        "2) infra/databricks",
        "3) agents/bedrock/terraform",
        "4) infra/bundles",
    ]:
        assert step in text


def test_gated_workflows_do_not_hardcode_secret_values():
    for name in _GATED:
        text = _text(name)
        # Credentials are referenced via GitHub secrets, never literal values.
        assert "${{ secrets." in text
        assert "dapi" not in text  # no literal Databricks token
        assert "AKIA" not in text  # no literal AWS access key
