"""Validate the GitHub Actions workflows: well-formed YAML + intended triggers/commands.

YAML 1.1 parses the `on:` key as the boolean True, so trigger config is read from either
key. We do NOT trigger any cloud workflow — this only inspects the files.
"""

from __future__ import annotations

import re
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
    assert "-out=tfplan" in plan_script, "nothing is planned for the approver to read"
    assert "apply -input=false tfplan" in apply_script, (
        "apply must run a SAVED plan, not a fresh one"
    )
    assert "-auto-approve" not in apply_script, "an unreviewed apply is back in the pipeline"


def test_the_uploaded_plan_artifact_cannot_carry_secrets():
    """A `.tfplan` stores every variable value verbatim, including `sensitive = true` ones.

    `sensitive` redacts console output; it does not redact the file. Uploading the binary
    plan published `DATABRICKS_CLIENT_SECRET` in cleartext to anyone with repo read, for
    five days, recoverable with `terraform show -json tfplan`. It was a new exposure created
    by the commit that added the plan/apply split — previously the secret only ever lived in
    the runner's memory.

    Only the `show` render may leave the runner: that one honours `sensitive`.
    """
    doc = _load("deploy")
    for job in doc["jobs"].values():
        for step in job.get("steps", []):
            if "upload-artifact" not in str(step.get("uses", "")):
                continue
            paths = str(step.get("with", {}).get("path", ""))
            assert "tfplan" not in paths, (
                f"the workflow uploads {paths!r} — a .tfplan carries every variable value "
                "in cleartext, including the Databricks client secret"
            )


def test_each_layer_is_planned_after_the_layer_it_depends_on():
    """Plan-all-then-apply-all cannot work here, and would fail mid-deploy.

    `infra/databricks` and `agents/bedrock/terraform` read
    `data.terraform_remote_state.aws`. Before `infra/aws` applies, those outputs do not
    exist, so planning them first fails on "Unsupported attribute" — on a first deploy, or
    on any deploy that changes an upstream output. The apply job plans each layer directly
    before applying it.
    """
    apply_script = _run_script(_load("deploy")["jobs"]["apply"])
    plan_pos = apply_script.find("plan -input=false -out=tfplan")
    apply_pos = apply_script.find("apply -input=false tfplan")
    assert plan_pos != -1 and apply_pos != -1, "the apply job does not plan before applying"
    assert plan_pos < apply_pos, "the apply job applies before it plans"
    assert "for dir in ${TF_LAYERS}" in apply_script, (
        "the apply job does not iterate the layers, so plan/apply are not interleaved "
        "per layer and an upstream output cannot be read by the layer below it"
    )


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
    """An action ref must be an immutable commit SHA. Branches AND tags are both mutable.

    ALLOWLIST. The first version asserted `not endswith(("@main", "@master"))` — it
    blacklisted two spellings of the finding rather than the property being demanded, so
    every one of these shipped green:

        uses: aws-actions/configure-aws-credentials@v4   # in a job with id-token: write
        uses: gitleaks/gitleaks-action@v2
        uses: actions/setup-python@v5

    A git tag is not a version, it is a movable pointer: `git tag -f v4 && git push --force`
    silently re-aims `@v4` at new code, on the next run, with no diff in this repo. That is
    the same supply-chain path `@main` opens — the docstring named the mechanism ("re-resolved
    every run") and then checked for two literal strings instead of the mechanism.

    Demanding 40 hex characters is short. Enumerating every mutable ref spelling is endless.
    """
    for line in _text(name).splitlines():
        stripped = line.strip()
        if "uses:" not in stripped:
            continue
        if "./.github/workflows" in stripped:  # a local reusable workflow, not an action
            continue
        ref = stripped.split("uses:", 1)[1].split("#", 1)[0].strip()
        assert "@" in ref, f"{name} uses an action with no ref at all: {stripped}"
        assert re.fullmatch(r"[0-9a-f]{40}", ref.rsplit("@", 1)[1]), (
            f"{name} pins an action to a MUTABLE ref: {stripped}\n"
            "Tags and branches can both be re-pointed at new code without a diff here. "
            "Pin to the full commit SHA, with the version in a trailing comment."
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


def test_the_oidc_deploy_role_is_defined_in_terraform_and_sub_scoped():
    """The most privileged identity in the system must be reviewable and pinned.

    The workflows assume `AWS_DEPLOY_ROLE_ARN`, and for a while that role was defined in no
    Terraform at all — so the credential that applies IAM, KMS and VPCs across three layers
    was unreviewable, and its trust condition (the thing that stops any fork with
    `id-token: write` assuming it) could not be read. "IaC only" was violated by the one
    credential that matters most.
    """
    oidc = (_WORKFLOWS.parents[1] / "infra" / "aws" / "bootstrap" / "oidc.tf").read_text(
        encoding="utf-8"
    )
    assert "aws_iam_openid_connect_provider" in oidc, "no OIDC provider is defined"
    assert "token.actions.githubusercontent.com:sub" in oidc, (
        "the trust policy does not condition on `sub` — any repo/branch/fork with "
        "id-token: write could assume this role"
    )
    # The sub must pin to a specific repo, not a bare wildcard.
    assert "repo:${var.github_repository}" in oidc
    assert ":ref:refs/heads/*" not in oidc and "sub:*" not in oidc, (
        "the sub condition is a wildcard — it trusts every branch and every PR"
    )


def test_destroy_continues_to_later_layers_when_one_fails():
    """A teardown must not stop at its first failure — the cost is at the bottom.

    The layer steps ran with the default `if: success()`. When layer 3 failed on two
    non-empty versioned buckets, step 4 never ran and left the whole `infra/aws` layer —
    MSK, VPC, NAT gateway — ACTIVE and billing (run 29686453021). The layer that costs the
    most per hour was protected by the least, and the run reported "failure" for a teardown
    that had in fact destroyed nothing below the failure point.

    The condition must be SCOPED, not blanket: a bare `always()` would run the teardown even
    when the typed-confirmation guard REJECTED the request, which inverts the guard.
    """
    doc = _load("destroy")
    steps = doc["jobs"]["destroy"]["steps"]
    layers = [s for s in steps if re.match(r"^\d+\) ", s.get("name", ""))]
    assert len(layers) == 4, f"expected 4 teardown layers, found {[s['name'] for s in layers]}"

    for step in layers:
        condition = step.get("if", "")
        assert "always()" in condition, (
            f"{step['name']} runs only if every earlier step succeeded, so one failing layer "
            "leaves every layer below it standing — and billing"
        )
        # The guard must still be able to stop it.
        assert "steps.guard.outcome == 'success'" in condition, (
            f"{step['name']} would run even when the typed-confirmation guard rejected the "
            "request — that is not a guard"
        )
        assert "steps.creds.outcome == 'success'" in condition, (
            f"{step['name']} would run without credentials having been assumed"
        )

    # ...but the BASE layer is the exception, and it is not a detail. Both infra/databricks
    # and agents/bedrock read its outputs through remote state, so destroying it while either
    # still holds resources leaves those layers unable to plan — and therefore unable to ever
    # destroy their survivors. Run 29686711080 did exactly that and wedged infra/databricks
    # across three subsequent runs. Continue past failures, but never past a DEPENDENCY's.
    base = next(s for s in layers if s["name"].startswith("4) "))
    for upstream in ("bedrock", "databricks"):
        assert f"steps.{upstream}.outcome == 'success'" in base["if"], (
            f"infra/aws is destroyed even when the {upstream} layer failed — that removes "
            "the remote-state outputs its configuration reads and strands it permanently"
        )


def test_destroy_fails_the_job_if_any_layer_survived():
    """Continuing past a failure must never be mistaken for succeeding through it."""
    steps = _load("destroy")["jobs"]["destroy"]["steps"]
    summary = [s for s in steps if "summary" in s.get("name", "").lower()]
    assert summary, "destroy continues past failures but never reports which layers survived"
    step = summary[0]
    assert "exit 1" in step["run"], "the summary reports survivors without failing the job"
    # The outcomes are interpolated in `env:`, not inline in `run:` — a step that pastes
    # `${{ steps.x.outcome }}` straight into the script would be reading attacker-free but
    # still-untrusted expression output at shell level. Search the whole step.
    rendered = step["run"] + "\n" + "\n".join(str(v) for v in step.get("env", {}).values())
    for layer in ("bundles", "bedrock", "databricks", "aws"):
        assert f"steps.{layer}.outcome" in rendered, (
            f"the teardown summary does not check the {layer} layer, so its failure is silent"
        )


def test_every_layer_that_owns_buckets_is_emptied_before_it_is_destroyed():
    """S3 refuses to delete a bucket that still holds objects, and `force_destroy` cannot
    rescue a teardown: `terraform destroy` plans from PRIOR STATE and never applies attribute
    changes, so the provider reads the flag recorded in state rather than the one in config.

    The first version of this guard checked ONE layer — infra/databricks, the one that had
    failed. The identical trap was already waiting on `infra/aws` (651 MB of IEEE-CIS data)
    and `agents/bedrock` (the regulatory corpus), neither declaring force_destroy. Fixing the
    instance instead of the property is how the second and third copies stay hidden until
    they each cost a teardown.

    So this asserts the PROPERTY: every Terraform layer the destroy tears down is preceded by
    an emptying step for that same layer.
    """
    steps = _load("destroy")["jobs"]["destroy"]["steps"]
    names = [s.get("name", "") for s in steps]

    layers = {
        "2) agents/bedrock/terraform": "agents/bedrock/terraform",
        "3) infra/databricks": "infra/databricks",
        "4) infra/aws": "infra/aws",
    }
    for step_name, layer in layers.items():
        destroy_at = next((i for i, n in enumerate(names) if n == step_name), None)
        assert destroy_at is not None, f"the destroy no longer has a step '{step_name}'"

        empty_at = next(
            (
                i
                for i, s in enumerate(steps)
                if "Empty the buckets" in s.get("name", "") and layer in s.get("run", "")
            ),
            None,
        )
        assert empty_at is not None, (
            f"nothing empties the buckets owned by {layer}, so its destroy fails with "
            "BucketNotEmpty on anything it holds"
        )
        assert empty_at < destroy_at, (
            f"{layer} is emptied AFTER it is destroyed, which is no ordering at all"
        )


def test_the_bucket_emptying_handles_delete_markers_and_pagination():
    """Two halves that are easy to leave out, each silent when missing.

    A bucket holding only DELETE MARKERS is still BucketNotEmpty while `aws s3 ls` shows
    nothing. And `list-object-versions` caps at 1000 keys, so one pass quietly leaves the rest
    on any bucket that outgrew that — the delete then fails for a reason the log reads as
    permissions.
    """
    script = (Path(__file__).resolve().parents[2] / "scripts/empty_layer_buckets.sh").read_text(
        "utf-8"
    )
    assert "DeleteMarkers" in script, "only object versions are deleted; markers would remain"
    assert "max-keys" in script and "while" in script, "the version listing is not paginated"
    assert "terraform" in script and "state list" in script, (
        "bucket names are not read from the state being destroyed — this could reach buckets "
        "the layer does not own"
    )


def test_the_secret_purge_can_only_touch_secrets_already_being_deleted():
    """Force-deleting a secret is irreversible, so this step's SCOPE is the whole safety story.

    It exists because a secret scheduled for deletion still owns its name, and Secrets
    Manager refuses to re-create it — which blocked deploy run 29706126775 after MSK had
    already spent 26 minutes creating.

    Two filters, and dropping either is catastrophic in a different way: without the
    `DeletedDate` test it force-deletes LIVE secrets, and without the prefix it reaches every
    secret in the account — including other projects'.
    """
    steps = _load("deploy")["jobs"]["apply"]["steps"]
    purge = [s for s in steps if "pending deletion" in s.get("name", "").lower()]
    assert purge, "nothing purges secrets left in limbo, so a rebuild is blocked for a week"
    step = purge[0]
    script = step["run"]

    assert "DeletedDate!=null" in script, (
        "the purge does not restrict itself to secrets ALREADY scheduled for deletion — as "
        "written it would force-delete live secrets, irreversibly"
    )
    assert "starts_with(Name, '${PREFIX}')" in script, (
        "the purge is not scoped to this project/environment's secret prefix — it would "
        "reach every secret in the account, including other projects'"
    )
    prefix = step.get("env", {}).get("PREFIX", "")
    assert prefix.startswith("fintelliguard/") and "inputs.environment" in prefix, (
        f"the purge prefix {prefix!r} is not pinned to this project and the target environment"
    )
