"""The agent must invoke an INFERENCE PROFILE, not a bare foundation-model id.

Claude Haiku 4.5 has no on-demand throughput on the bare model id: invoking it raises
ValidationException ("Retry ... with an inference profile"), which the agent surfaces as an
opaque accessDenied at InvokeAgent time — the model resolves at apply, the deploy goes green,
and every verdict is then denied (deploy run 29894311393). So the agent's foundation model
must be a cross-region inference profile id, and its IAM must grant InvokeModel on BOTH the
profile and the base model it routes to. This parses the Bedrock layer and asserts both, so the
bare id cannot silently return.
"""

from __future__ import annotations

from pathlib import Path

import hcl2

_TF = Path(__file__).resolve().parents[2] / "agents" / "bedrock" / "terraform"

# A cross-region inference profile id is the model id prefixed with a routing scope.
_PROFILE_PREFIXES = ("eu.", "us.", "apac.", "global.")


def _unwrap(value):
    return value[0] if isinstance(value, list) and len(value) == 1 else value


def _variables() -> dict:
    with open(_TF / "variables.tf", encoding="utf-8") as handle:
        doc = hcl2.load(handle)
    out = {}
    for block in doc.get("variable", []):
        for name, body in block.items():
            out[name] = _unwrap(body.get("default"))
    return out


def test_agent_invokes_an_inference_profile_not_a_bare_model_id():
    default = _variables().get("foundation_model", "")
    assert default.startswith(_PROFILE_PREFIXES), (
        f"foundation_model default {default!r} is a bare model id — Haiku 4.5 has no on-demand "
        "throughput on the bare id, so the agent's verdicts are denied. Use an inference profile "
        f"(one of {_PROFILE_PREFIXES})."
    )


def test_iam_grants_invoke_on_the_profile_and_the_base_model():
    """Invoking via a cross-region profile needs InvokeModel on the profile AND the base FM in
    each routed region; a grant on only one is denied at runtime."""
    data_tf = (_TF / "data.tf").read_text(encoding="utf-8")
    iam_tf = (_TF / "iam.tf").read_text(encoding="utf-8")
    assert "inference-profile/${var.foundation_model}" in data_tf, (
        "foundation_model_arn does not point at an inference profile"
    )
    assert "var.foundation_model_base_id" in data_tf, (
        "the base model (for the FM ARN grant) is not derived"
    )
    assert "foundation_model_invoke_arns" in iam_tf, (
        "the agent's InvokeModel is not granted on the profile + base-model ARNs"
    )
