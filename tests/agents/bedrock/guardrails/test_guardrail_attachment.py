"""Prove the guardrail is ATTACHED and ENABLED — not merely declared.

Guards a real bug: `guardrail.tf` declared a full policy and `agent.tf` never bound it, so
every Tier-2 verdict shipped with no PII redaction, no denied-topic filter and no grounding
check, while four green tests asserted the policy classes were "covered". Those tests
grepped `guardrail.tf` for string literals. A declaration is not an attachment, and a
string match cannot tell the difference.

Each test here fails on a distinct way the control can be silently defeated:

  * detached      — `guardrail_configuration` removed from the agent
  * dangling      — bound to a guardrail that does not exist
  * hardcoded     — bound to a literal id instead of the guardrail we manage
  * DRAFT-bound   — bound to a mutable snapshot, so no decision is attributable
  * detuned       — declared but with strength/threshold/action set to a no-op
  * drifted       — Terraform and `policy.py` disagree, so the red-team score describes a
                    policy that is not the one deployed

`scripts/gate_proof.py` plants each of those mutations and asserts these tests actually go
red — because a gate nobody has attacked is a gate nobody has tested.
"""

from __future__ import annotations

import pytest

from agents.bedrock.guardrails.policy import GuardrailPolicy
from agents.bedrock.terraform_model import bedrock_model

AGENT = "aws_bedrockagent_agent"
GUARDRAIL = "aws_bedrock_guardrail"
GUARDRAIL_VERSION = "aws_bedrock_guardrail_version"

# ALLOWLISTS, not blacklists. This is the whole lesson of this file's second round.
#
# The first version blacklisted the off-states — `NEUTERED = {"NONE", "DISABLED", ""}` —
# and one indirection walked past it:
#
#     locals { pa_strength = "NONE" }
#     filters_config { type = "PROMPT_ATTACK"  input_strength = local.pa_strength }
#
# The parser returns the unevaluated expression `"${local.pa_strength}"`, which is not in
# the blacklist, so the filter read as ACTIVE while being off. Seven tests passed with
# PROMPT_ATTACK and PII redaction both disabled. `locals`/`var` indirection is idiomatic
# Terraform, not sabotage.
#
# A blacklist has to enumerate every way to be wrong; an allowlist enumerates the few ways
# to be right, so anything unrecognised — an indirection, a typo, a new AWS value — fails
# CLOSED. Replacing "assert the string is in the file" with "assert the value is not in
# this set of bad strings" is a better test of the same kind, and it fails the same way.
ACTIVE_FILTER_STRENGTHS = {"LOW", "MEDIUM", "HIGH"}
REDACTING_PII_ACTIONS = {"ANONYMIZE", "BLOCK"}


@pytest.fixture(scope="module")
def model():
    return bedrock_model()


@pytest.fixture(scope="module")
def agent(model):
    return model.one(AGENT)


@pytest.fixture(scope="module")
def guardrail(model):
    return model.one(GUARDRAIL)


def _binding(agent) -> dict:
    """The agent's guardrail binding, or fail loudly explaining what that means."""
    config = agent.get("guardrail_configuration")
    assert config, (
        "the agent declares NO guardrail_configuration — the guardrail may exist in AWS, "
        "but it never runs, and every verdict ships unfiltered"
    )
    # Provider 6.x models this as a list of objects.
    assert len(config) == 1, f"expected exactly one guardrail binding, got {len(config)}"
    return config[0]


# --------------------------------------------------------------------------- #
# Attachment — the check whose absence was the bug
# --------------------------------------------------------------------------- #


def test_agent_binds_a_guardrail_that_exists(model, agent):
    """The binding resolves to the guardrail this layer actually declares."""
    ref = model.resolve(_binding(agent)["guardrail_identifier"])
    assert ref.type == GUARDRAIL, (
        f"the agent's guardrail_identifier points at {ref.address}, which is not a "
        f"{GUARDRAIL} — the binding exists but guards nothing"
    )


def test_agent_binds_an_immutable_version_not_draft(model, agent):
    """A DRAFT binding cannot answer 'which policy was in force for this decision?'.

    DRAFT mutates in place. Bound to it, the agent's policy silently changes under every
    apply and no verdict is attributable to a fixed set of rules — which is the whole
    point of the record-keeping obligation.
    """
    version = _binding(agent)["guardrail_version"]
    assert "DRAFT" not in str(version).upper(), (
        "the agent is bound to the DRAFT guardrail: the policy can change under it with "
        "no version bump, so no past verdict can be tied to the rules that produced it"
    )
    ref = model.resolve(version)
    assert ref.type == GUARDRAIL_VERSION


def test_the_version_is_replaced_when_the_policy_changes(model):
    """An "immutable version" that never re-mints pins the agent to version 1 forever.

    `aws_bedrock_guardrail_version` has no argument derived from the policy body — only
    `guardrail_arn`, which does not change when the policy does — so Terraform saw no diff
    on any policy edit and the agent kept running version 1 while DRAFT drifted underneath.
    The comment above the resource claimed the opposite ("every apply that changes the
    policy mints a new version"), which made it a comment denying a hole.
    """
    version = model.one(GUARDRAIL_VERSION)

    lifecycle = version.get("lifecycle")
    assert lifecycle, (
        "the version has no lifecycle block, so nothing replaces it when the policy changes "
        "— the agent stays pinned to version 1 while DRAFT drifts"
    )
    block = lifecycle[0] if isinstance(lifecycle, list) else lifecycle
    triggers = block.get("replace_triggered_by")
    assert triggers, "replace_triggered_by is absent: a policy edit produces no new version"
    assert any(GUARDRAIL in str(t) for t in triggers), (
        f"the version is replaced by {triggers}, which is not the guardrail it snapshots"
    )

    # The description must derive from the policy, so a version is self-identifying in the
    # console rather than an opaque number.
    assert "fingerprint" in str(version.get("description", "")), (
        "the version's description does not derive from the policy, so two different "
        "policies produce indistinguishable snapshots"
    )


def test_the_bound_version_snapshots_the_bound_guardrail(model, agent):
    """The version the agent uses must be a snapshot of the guardrail it names.

    Without this, `guardrail.tf` can be hardened while the agent quietly runs a version cut
    from a different guardrail — both references resolve, and everything looks correct.
    """
    binding = _binding(agent)
    guardrail_ref = model.resolve(binding["guardrail_identifier"])
    version_ref = model.resolve(binding["guardrail_version"])

    version_body = model.get(version_ref.type, version_ref.name)
    snapshotted = model.resolve(version_body["guardrail_arn"])

    assert snapshotted.address == guardrail_ref.address, (
        f"the agent runs {version_ref.address}, which snapshots {snapshotted.address}, "
        f"but binds guardrail {guardrail_ref.address} — the deployed policy is not the "
        "one this layer declares"
    )


# --------------------------------------------------------------------------- #
# Enablement — declared is not the same as switched on
# --------------------------------------------------------------------------- #


def test_prompt_attack_filter_is_declared_and_active_on_input(guardrail):
    """PROMPT_ATTACK must actually screen input.

    AWS only supports this filter on input (output_strength must be NONE), so the input
    strength is the entire control. The old test asserted the literal `type =
    "PROMPT_ATTACK"` appeared in the file — true even with input_strength = "NONE".
    """
    filters = guardrail["content_policy_config"][0]["filters_config"]
    match = [f for f in filters if f["type"] == "PROMPT_ATTACK"]
    assert match, "the prompt-attack filter is gone, but policy.py still claims it blocks"
    strength = match[0].get("input_strength", "")
    assert strength in ACTIVE_FILTER_STRENGTHS, (
        f"PROMPT_ATTACK input_strength is {strength!r}, which is not one of "
        f"{sorted(ACTIVE_FILTER_STRENGTHS)}. Either it is switched off, or it is an "
        "indirection (`local.x`, `var.y`) this test cannot evaluate — and a control whose "
        "state cannot be read here is a control this test is not checking. Write the value."
    )


def test_pii_entities_match_the_policy_model_and_are_anonymised(guardrail):
    """Every PII entity `policy.py` claims to redact is redacted in Terraform.

    Catches both directions: an entity dropped from Terraform (the model over-reports
    coverage) and an entity whose action is detuned to a no-op.
    """
    configs = guardrail["sensitive_information_policy_config"][0]["pii_entities_config"]
    declared = {c["type"]: c["action"] for c in configs}
    modelled = set(GuardrailPolicy().pii_entities)

    assert modelled <= set(declared), (
        f"policy.py claims to redact {sorted(modelled - set(declared))}, but guardrail.tf "
        "does not declare them — the red-team PII score describes a policy that is not "
        "deployed"
    )
    for entity in modelled:
        assert declared[entity] in REDACTING_PII_ACTIONS, (
            f"PII entity {entity} has action={declared[entity]!r}, which is not one of "
            f"{sorted(REDACTING_PII_ACTIONS)} — it is declared but not redacting, or it is "
            "an indirection this test cannot evaluate. Either way the dataset card's "
            "anonymisation promise is not backed by the deployed policy."
        )


def test_denied_topics_match_the_policy_model(guardrail):
    """The denied topics Terraform enforces are the ones `policy.py` is scored against."""
    topics = guardrail["topic_policy_config"][0]["topics_config"]
    declared = {t["name"]: t["type"] for t in topics}
    modelled = set(GuardrailPolicy().denied_topics)

    assert modelled <= set(declared), (
        f"policy.py denies {sorted(modelled - set(declared))} but guardrail.tf does not"
    )
    for topic in modelled:
        assert declared[topic] == "DENY", (
            f"topic {topic} is declared with type={declared[topic]!r}, not DENY — it is "
            "catalogued, not enforced"
        )


def test_grounding_thresholds_match_the_policy_model(guardrail):
    """Terraform's grounding threshold is the number `policy.py` models and the docs print.

    Nothing cross-checked these before: `guardrail.tf` could drop to 0.01 while `policy.py`
    kept modelling 0.75 and the generated AI-Act document kept publishing 0.75 as the
    deployed control.
    """
    filters = guardrail["contextual_grounding_policy_config"][0]["filters_config"]
    declared = {f["type"]: float(f["threshold"]) for f in filters}
    policy = GuardrailPolicy()

    assert declared.get("GROUNDING") == pytest.approx(policy.grounding_threshold), (
        f"guardrail.tf grounds at {declared.get('GROUNDING')} but policy.py models "
        f"{policy.grounding_threshold} — the generated governance docs publish the model's "
        "number, not the deployed one"
    )
    assert declared.get("RELEVANCE") == pytest.approx(policy.relevance_threshold), (
        f"guardrail.tf relevance {declared.get('RELEVANCE')} != policy.py "
        f"{policy.relevance_threshold}"
    )
