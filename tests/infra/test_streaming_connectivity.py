"""The private Databricks→MSK path must stay wired, in Terraform, or it silently breaks.

Three things make a classic Spark cluster able to reach MSK in this VPC, and none of them is
caught by `terraform validate` (all reference-valid) or a unit test (no cloud):

  1. The MSK security group must admit the Databricks data-plane SG on 9098 (IAM SASL). Drop
     this one ingress and the consumer's TCP SYN is discarded at the broker — the read hangs,
     then times out, three layers downstream, as "no data" rather than "blocked".
  2. An instance profile must wrap the MSK IAM role, or a cluster has no way to assume it.
  3. The Databricks cross-account role must be allowed to iam:PassRole that role, and the
     instance profile must be registered — or launching a cluster with it is denied.

This parses the Terraform and asserts all three, so a refactor that removes any of them fails
here, at PR time, instead of in the cloud after a green apply.
"""

from __future__ import annotations

from pathlib import Path

import hcl2

_ROOT = Path(__file__).resolve().parents[2]
_AWS_NETWORK = _ROOT / "infra" / "aws" / "network.tf"
_AWS_STREAMING = _ROOT / "infra" / "aws" / "streaming.tf"
_DBX_STREAMING = _ROOT / "infra" / "databricks" / "streaming.tf"


def _resources(tf_path: Path, resource_type: str, name: str) -> list[dict]:
    with open(tf_path, encoding="utf-8") as handle:
        doc = hcl2.load(handle)
    out = []
    for block in doc.get("resource", []):
        typed = block.get(resource_type, {})
        if name in typed:
            out.append(typed[name])
    return out


def _as_list(value) -> list:
    return value if isinstance(value, list) else [value]


def _msk_ingress_rules() -> list[dict]:
    msk = _resources(_AWS_NETWORK, "aws_security_group", "msk")
    assert msk, "aws_security_group.msk not found — the parser or layout changed"
    rules: list[dict] = []
    for body in msk:
        rules.extend(_as_list(body.get("ingress", [])))
    return rules


def test_msk_admits_the_databricks_data_plane_on_the_iam_sasl_port():
    hits = [
        rule
        for rule in _msk_ingress_rules()
        if str(rule.get("from_port")) == "9098"
        and any(
            "databricks_data_plane" in str(sg) for sg in _as_list(rule.get("security_groups", []))
        )
    ]
    assert hits, (
        "the MSK security group has no ingress on 9098 from the Databricks data-plane SG — a "
        "classic Spark cluster cannot reach the brokers and the stream will hang as 'no data'"
    )


def test_an_instance_profile_wraps_the_msk_iam_role():
    profiles = _resources(_AWS_STREAMING, "aws_iam_instance_profile", "msk_access")
    assert profiles, (
        "aws_iam_instance_profile.msk_access is missing — no cluster can assume the MSK role"
    )
    assert any("msk_access" in str(body.get("role", "")) for body in profiles)


def test_cross_account_may_pass_the_role_and_the_profile_is_registered():
    passes = _resources(_DBX_STREAMING, "aws_iam_role_policy", "cross_account_pass_msk")
    assert passes, (
        "the cross-account role is not granted iam:PassRole for the MSK role — cluster launch is denied"
    )
    registered = _resources(_DBX_STREAMING, "databricks_instance_profile", "msk_access")
    assert registered, "the MSK instance profile is never registered with Databricks"
