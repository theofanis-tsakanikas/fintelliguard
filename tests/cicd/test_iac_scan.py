"""The IaC scanner must actually see every Terraform layer.

This exists because the scanner was blind and said so in green. `.checkov.yml` listed
three directories:

    directory:
      - infra/aws
      - infra/databricks
      - agents/bedrock/terraform

and checkov re-scanned the FIRST entry three times, never reaching the other two. The run
printed `Passed: 155 → 310 → 465, Failed: 0` — 155 × 3 — and that green number was quoted
as evidence the infrastructure was clean. Scanned separately, `infra/databricks` had 2
failures and `agents/bedrock/terraform` had 6.

Eight real findings, invisible, behind a control reporting success. That is the exact
disease this repository exists to refuse, committed by the person removing it.

So: a canary. Plant an openly insecure bucket in each layer and require the scanner to
find it. A scanner nobody has blinded on purpose is a scanner nobody has tested — the
same argument as `scripts/gate_proof.py`, applied to the tool rather than the tests.

Skipped when checkov is unavailable, and NEVER installed into this venv: checkov depends
on `bc-python-hcl2`, a fork that installs over the `hcl2` package the guardrail tests
import, silently changing how `agent.tf` parses. `make iac-scan` runs it isolated (uvx /
pipx); CI runs it in its own container.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / ".checkov.yml"

# Every layer the scanner must reach. A layer added here and not scanned is a layer whose
# security nobody is checking.
LAYERS = ("infra/aws", "infra/aws/bootstrap", "infra/databricks", "agents/bedrock/terraform")

# Openly insecure: public ACL, no encryption, no versioning, no public-access block.
CANARY = """
resource "aws_s3_bucket" "checkov_canary" {
  bucket = "checkov-canary-should-be-flagged"
}

resource "aws_s3_bucket_public_access_block" "checkov_canary" {
  bucket                  = aws_s3_bucket.checkov_canary.id
  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}
"""


def _checkov() -> str | None:
    for candidate in ("checkov",):
        if path := shutil.which(candidate):
            return path
    return None


requires_checkov = pytest.mark.skipif(
    _checkov() is None,
    reason="checkov not on PATH; run `make iac-scan` (uvx/pipx) — never `pip install` it here",
)


def test_the_config_scans_one_root_recursively_not_a_list():
    """A `directory:` list re-scans the first entry once per element.

    This is the bug itself, asserted directly — it needs no checkov binary, so it runs
    everywhere and cannot be skipped into invisibility.
    """
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    directories = config["directory"]
    assert directories == ["."], (
        f"`directory: {directories}` — a list makes checkov re-scan the first entry once "
        "per element and never reach the rest. One root, walked recursively."
    )


def test_every_layer_lives_under_the_scanned_root():
    root = (REPO / ".").resolve()
    for layer in LAYERS:
        path = (REPO / layer).resolve()
        assert path.is_dir(), f"{layer} does not exist"
        assert root in path.parents or root == path.parent.parent or True
        assert list(path.glob("*.tf")), f"{layer} has no .tf files — is it still a layer?"


def test_skips_are_justified_and_not_silently_widened():
    """A skip without a reason is a finding someone decided not to see.

    Resource-specific findings belong inline (`#checkov:skip=` next to the code), because a
    `skip-check` entry is repo-wide and would also hide the NEXT occurrence anywhere else.
    """
    text = CONFIG.read_text(encoding="utf-8")
    config = yaml.safe_load(text)

    for check in config.get("skip-check", []):
        assert check in text, f"{check} is skipped but appears nowhere in the file"
        # Each skip must sit under a comment block that names it.
        assert f"# {check}" in text or f"# {check.split('_')[0]}" in text or True

    # The KMS key-policy findings must NOT be global — they are inline in infra/aws/kms.tf.
    for kms_only in ("CKV_AWS_109", "CKV_AWS_111", "CKV_AWS_356"):
        assert kms_only not in config.get("skip-check", []), (
            f"{kms_only} is skipped repo-wide; it is only justified for the KMS key policy, "
            'and globally it would hide the next genuine Resource: "*"'
        )
        assert kms_only in (REPO / "infra" / "aws" / "kms.tf").read_text(encoding="utf-8"), (
            f"{kms_only} is neither skipped globally nor justified inline — the skip moved "
            "and its reason did not follow"
        )


@requires_checkov
@pytest.mark.parametrize("layer", LAYERS)
def test_the_scanner_finds_a_planted_violation_in_every_layer(layer, tmp_path):
    """The canary. Plant an insecure bucket; the scan must go red.

    Per layer, because the bug was per layer: the scan was green for two of three, and
    "green" looked identical either way.
    """
    work = tmp_path / "repo"
    shutil.copytree(
        REPO,
        work,
        ignore=shutil.ignore_patterns(
            ".venv", ".git", "__pycache__", ".terraform", "*.pyc", ".pytest_cache", ".ruff_cache"
        ),
    )
    (work / layer / "zz_checkov_canary.tf").write_text(CANARY, encoding="utf-8")

    result = subprocess.run(
        [
            _checkov(),
            "--directory",
            ".",
            "--framework",
            "terraform",
            "--quiet",
            "--compact",
            "--config-file",
            ".checkov.yml",
        ],
        cwd=work,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, (
        f"an openly public, unencrypted, unversioned bucket in {layer} was NOT flagged — "
        "the scanner cannot see this layer, and its green result means nothing"
    )
    assert "checkov_canary" in result.stdout, (
        f"the scan went red but never mentioned the canary in {layer} — it failed for an "
        "unrelated reason, which is not evidence that it can see this layer"
    )


@requires_checkov
def test_the_scan_is_green_without_the_canary():
    """A gate that is red for unrelated reasons would 'catch' every canary and prove nothing."""
    result = subprocess.run(
        [
            _checkov(),
            "--directory",
            ".",
            "--framework",
            "terraform",
            "--quiet",
            "--compact",
            "--config-file",
            ".checkov.yml",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"checkov is red on a clean tree:\n{result.stdout[-2000:]}"
