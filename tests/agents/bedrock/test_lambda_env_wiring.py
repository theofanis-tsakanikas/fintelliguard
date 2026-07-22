"""Every environment variable the Lambda READS must be one Terraform SETS.

`handler._default_dependencies` reads `os.environ["MOSAIC_ENDPOINT_URL"]`,
`DATABRICKS_TOKEN_SECRET_ID`, `ONLINE_FEATURE_BUCKET`, `ONLINE_FEATURE_KEY`. If `lambda.tf`
does not set one of them, the Lambda raises `KeyError` on its FIRST real invocation — after a
green deploy, in the Tier-2 request path, surfacing to the agent as a scoring outage. A unit
test cannot catch it (it injects `Dependencies`), and `terraform validate` cannot (the string
is an env lookup, not a reference). So this parses both sides and asserts the read set is a
subset of the set set — referential integrity across the Python/Terraform boundary.
"""

from __future__ import annotations

import re
from pathlib import Path

import hcl2

_ROOT = Path(__file__).resolve().parents[3]
_LAMBDA_TF = _ROOT / "agents" / "bedrock" / "terraform" / "lambda.tf"
_LAMBDA_SRC = _ROOT / "agents" / "bedrock" / "lambda"

_ENVIRON = re.compile(r"""os\.environ\[\s*["']([A-Z_][A-Z0-9_]*)["']\s*\]""")


def _unwrap(value):
    return value[0] if isinstance(value, list) and len(value) == 1 else value


def _tf_env_keys() -> set[str]:
    with open(_LAMBDA_TF, encoding="utf-8") as handle:
        doc = hcl2.load(handle)
    for block in doc.get("resource", []):
        fn = block.get("aws_lambda_function", {})
        for body in fn.values():
            env = _unwrap(body.get("environment"))
            if isinstance(env, dict):
                return set(_unwrap(env.get("variables", {})).keys())
    return set()


def _environ_reads() -> set[str]:
    reads: set[str] = set()
    for src in _LAMBDA_SRC.glob("*.py"):
        reads.update(_ENVIRON.findall(src.read_text(encoding="utf-8")))
    return reads


def test_every_env_var_the_lambda_reads_is_set_by_terraform():
    reads = _environ_reads()
    provided = _tf_env_keys()
    assert reads, "no os.environ reads found — the parser or the layout changed"
    missing = reads - provided
    assert not missing, (
        f"the Lambda reads env var(s) {sorted(missing)} that lambda.tf never sets — the "
        "function will KeyError on its first real invocation, after a green deploy"
    )


def test_the_online_feature_env_is_wired_end_to_end():
    """The specific wiring this change adds: the id->features online store."""
    provided = _tf_env_keys()
    assert {"ONLINE_FEATURE_BUCKET", "ONLINE_FEATURE_KEY"} <= provided, (
        "lambda.tf does not pass the online-feature store location, so S3OnlineFeatureStore "
        "cannot resolve a transaction id to its features"
    )
