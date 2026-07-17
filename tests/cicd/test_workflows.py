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


def _run_script(job: dict) -> str:
    """Every shell command a job runs, and nothing else.

    Deliberately excludes comments and step names: a test that greps the raw YAML cannot
    distinguish a command from a comment describing one.
    """
    return "\n".join(step["run"] for step in job["steps"] if "run" in step)


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
        # the IaC scanner, over ONE recursive root
        "checkov --directory . --framework terraform",
    ]:
        assert command in text, f"ci.yml no longer runs the Responsible-AI gate: {command}"


def test_ci_puts_checkov_on_path_so_the_scan_canary_cannot_silently_skip():
    """`tests/cicd/test_iac_scan.py` self-skips without checkov on PATH.

    A skip is indistinguishable from a pass in aggregate output. The canary — which plants
    an openly insecure bucket in each Terraform layer and requires the scanner to find it —
    is the only thing standing between us and another blind scanner reporting green, so it
    must actually RUN in CI.

    `pipx`, not `pip`: checkov pulls `bc-python-hcl2`, a fork that installs over the `hcl2`
    package the guardrail tests import and changes how the Terraform parses.
    """
    text = _text("ci")
    assert "pipx install checkov" in text, (
        "CI does not put checkov on PATH, so tests/cicd/test_iac_scan.py skips every run — "
        "the canary that proves the scanner can see each layer would be permanently dead-green"
    )
    assert "pip install checkov" not in text, (
        "checkov must not enter the project venv: its bc-python-hcl2 dependency shadows the "
        "hcl2 package the guardrail attachment tests parse Terraform with"
    )


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
    # Each layer consumes the previous one's remote-state outputs, so the order is a
    # contract, not a preference. Plan and apply share one TF_LAYERS list precisely so the
    # two can never drift apart.
    assert doc["env"]["TF_LAYERS"].split() == [
        "infra/aws",
        "infra/databricks",
        "agents/bedrock/terraform",
    ]
    # The bundle is step 4 and lives outside the Terraform loop.
    assert "bundle deploy" in _text("deploy")


def test_gated_workflows_do_not_hardcode_secret_values():
    for name in _GATED:
        text = _text(name)
        # Credentials are referenced via GitHub secrets, never literal values.
        assert "${{ secrets." in text
        assert "dapi" not in text  # no literal Databricks token
        assert "AKIA" not in text  # no literal AWS access key


# --------------------------------------------------------------------------- #
# The deploy path — every one of these guards something the pipeline actually did
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", _GATED)
def test_cloud_workflows_use_oidc_not_long_lived_keys(name):
    """No static IAM user keys anywhere near the AWS estate.

    Every gated workflow authenticated with `AWS_ACCESS_KEY_ID` /
    `AWS_SECRET_ACCESS_KEY`: non-rotating credentials with, necessarily, near-admin rights
    (they create IAM roles, KMS keys and VPCs across three layers), living indefinitely in
    GitHub secrets. `test_gated_workflows_do_not_hardcode_secret_values` passed throughout
    — it only checked that no literal `AKIA` string appeared, which blessed the pattern as
    correct and left migrating to OIDC with no test pressure behind it.
    """
    text = _text(name)
    doc = _load(name)
    assert "AWS_ACCESS_KEY_ID" not in text, "static AWS keys are back"
    assert "AWS_SECRET_ACCESS_KEY" not in text
    assert "role-to-assume" in text, f"{name} does not assume a role via OIDC"
    assert doc.get("permissions", {}).get("id-token") == "write", (
        f"{name} requests no id-token permission — OIDC cannot mint a token without it"
    )


def test_deploy_plans_before_it_applies():
    """CLAUDE.md: 'always plan before apply; never apply without reviewing the plan.'

    Deploy ran three consecutive `terraform apply -auto-approve` with no `plan` step at
    all. `environment:` gated the run, but GitHub environment approval fires before the
    first step executes — the approver approved a commit SHA, having never seen a diff.
    """
    doc = _load("deploy")
    jobs = doc["jobs"]
    assert "plan" in jobs and "apply" in jobs, "deploy must be a plan job and an apply job"
    assert jobs["apply"]["needs"] == "plan" or "plan" in jobs["apply"]["needs"]
    assert jobs["apply"].get("environment"), "the approval gate must sit on apply, not plan"
    assert not jobs["plan"].get("environment"), "planning must not need an approval"

    # Read the commands, not the file. The first draft of this test grepped the raw text
    # and matched the comment ABOVE the workflow explaining what it used to do — string
    # matching being unable to tell a control from a description of one is the whole theme.
    plan_script = _run_script(jobs["plan"])
    apply_script = _run_script(jobs["apply"])
    assert "-out=tfplan" in plan_script, "the plan is never saved, so apply cannot consume it"
    assert "apply -input=false tfplan" in apply_script, "apply must run the SAVED plan"
    assert "-auto-approve" not in apply_script, "an unreviewed apply is back in the pipeline"


def test_deploy_only_ships_what_ci_validated():
    """Deploy had no `needs:`, no branch filter, and re-ran none of the gates.

    A dispatch could target any branch, so unvalidated, lint-failing, red-team-failing code
    could be applied straight to the cloud. The Responsible-AI gates were enforced on pull
    requests only — never on the artifact that actually shipped.
    """
    doc = _load("deploy")
    validate = doc["jobs"].get("validate")
    assert validate, "deploy does not re-run the gates"
    assert validate["uses"].endswith("ci.yml"), "deploy must call the real CI workflow"
    assert "validate" in doc["jobs"]["plan"]["needs"]


@pytest.mark.parametrize("name", ["ci", *_GATED])
def test_no_workflow_pins_an_action_to_a_mutable_ref(name):
    """`@main` is re-resolved every run — a live supply-chain path into a privileged job."""
    for line in _text(name).splitlines():
        stripped = line.strip()
        if not stripped.startswith("- uses:") and "uses:" not in stripped:
            continue
        if "./.github/workflows" in stripped:  # a local reusable workflow, not an action
            continue
        assert not stripped.rstrip().endswith(("@main", "@master")), (
            f"{name} pins an action to a branch: {stripped}"
        )


def test_every_offered_environment_has_a_bundle_target():
    """The dropdown must not offer an environment the bundle cannot deploy.

    `deploy.yml` offered `prod`; `infra/bundles/databricks.yml` declares only `dev`. So
    `bundle deploy -t prod` failed — AFTER steps 1-3 had already applied AWS, Databricks
    and Bedrock infrastructure. A partial deploy, no rollback. `destroy.yml` had the mirror
    image: prod failed at step 1 and left everything standing. CI validated `-t dev` only,
    so nothing ever noticed.

    This is the same shape as the guardrail bug: a control naming a target that does not
    exist.
    """
    bundle = yaml.safe_load(
        (_WORKFLOWS.parents[1] / "infra" / "bundles" / "databricks.yml").read_text(encoding="utf-8")
    )
    declared = set(bundle.get("targets", {}))

    for name in ("deploy", "destroy"):
        triggers = _triggers(_load(name))
        offered = set(triggers["workflow_dispatch"]["inputs"]["environment"]["options"])
        assert offered <= declared, (
            f"{name}.yml offers {sorted(offered - declared)}, which "
            f"infra/bundles/databricks.yml does not declare (it has {sorted(declared)}) — "
            "the deploy fails part-way through, after real infrastructure has changed"
        )
