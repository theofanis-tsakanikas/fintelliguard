#!/usr/bin/env python3
"""Attack our own gates and prove they fight back.

A gate nobody has attacked is a gate nobody has tested. Every control in this repo is
green right now — but "green" is also what a control looks like when it is disconnected,
detuned, or asserting a tautology. This repo has shipped all three:

  * `guardrail.tf` declared a full policy that `agent.tf` never bound. Four tests covered
    the guardrail; all four grepped the file for string literals, so all four passed while
    every regulated verdict shipped unfiltered.
  * `test_schema_parity.py` compared dataclass field types that cannot diverge.
  * The DLT expectations ran on frames the pipeline had already filtered, so the
    data-quality metric could only ever read 100%.

Each of those was invisible for the same reason: nothing ever asked *what would this look
like if it were broken?* This script asks. For each attack it copies the repo to a
tmpdir, plants a violation the platform claims to refuse, runs the **real gate** against
the mutated copy, and demands the gate go red **for the right reason**.

Three rules make this a proof rather than a placebo:

1. **Baseline first.** Every gate must be green on the unmutated copy. A gate that is red
   for unrelated reasons would "block" every attack while proving nothing.
2. **A non-zero exit is not evidence.** An import error, a typo'd path, or a collection
   failure all exit non-zero. The named test must appear on a pytest `FAILED` line — a
   line that does not exist on a clean run — or the attack is scored INVALID, not BLOCKED.
3. **A no-op mutation is a failure of this script, not a win.** If the text an attack
   patches has moved, the attack silently does nothing and the gate stays green. That is
   reported as STALE and fails the run.

Usage:
    python -m scripts.gate_proof          # all attacks
    python -m scripts.gate_proof -v       # show each gate's output
    python -m scripts.gate_proof -k pii   # only attacks matching a substring
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# What the gate needs to run. Deliberately excludes .venv/.git/caches: the copy must be
# cheap enough that nobody is tempted to skip this script.
COPY = (
    "agents",
    "ml",
    "pipelines",
    "simulator",
    "tests",
    "pyproject.toml",
    ".github",
    "infra",
    # The teardown helpers the workflows call. Without this, a gate that reads
    # `scripts/empty_layer_buckets.sh` fails on the COPY for want of the file — which
    # gate_proof correctly refuses to score, since a red baseline proves nothing.
    "scripts",
    # The committed regulated docs, so `ml.governance.generate --check` has something to
    # diff against: the freshness gate only has teeth if a code mutation can make the staged
    # docs go stale relative to the staged code.
    "docs",
)

# Lines pytest prints on a CLEAN run too. A marker found on one of these is not a finding
# — this is the distinction between "the gate reported my violation" and "the gate printed
# the test's name while passing it".
_NOT_A_FINDING = ("PASSED", "collected", "passed", "no tests ran", "warnings summary")


@dataclass(frozen=True)
class Attack:
    """A violation the platform claims to refuse, and the gate that must catch it."""

    name: str
    # The real failure this simulates. Not decoration: it is why the attack is worth
    # keeping when someone later wonders whether to delete it.
    rationale: str
    path: str
    old: str
    new: str
    gate: str
    must_fail: str


ATTACKS: tuple[Attack, ...] = (
    Attack(
        name="guardrail-detached",
        rationale=(
            "The exact bug that shipped: the guardrail exists in AWS but the agent never "
            "binds it, so every verdict is unfiltered while the console looks healthy."
        ),
        path="agents/bedrock/terraform/agent.tf",
        old="""  guardrail_configuration = [{
    guardrail_identifier = aws_bedrock_guardrail.this.guardrail_id
    guardrail_version    = aws_bedrock_guardrail_version.this.version
  }]
""",
        new="",
        gate="tests/agents/bedrock/guardrails/test_guardrail_attachment.py",
        must_fail="test_agent_binds_a_guardrail_that_exists",
    ),
    Attack(
        name="guardrail-dangling",
        rationale=(
            "A rename leaves the binding pointing at a guardrail that no longer exists. "
            "Terraform catches this at plan time — in the cloud, after CI passed."
        ),
        path="agents/bedrock/terraform/agent.tf",
        old="guardrail_identifier = aws_bedrock_guardrail.this.guardrail_id",
        new="guardrail_identifier = aws_bedrock_guardrail.renamed_away.guardrail_id",
        gate="tests/agents/bedrock/guardrails/test_guardrail_attachment.py",
        must_fail="test_agent_binds_a_guardrail_that_exists",
    ),
    Attack(
        name="guardrail-hardcoded-id",
        rationale=(
            "Someone pastes a guardrail id from the console to 'unblock' a deploy. The "
            "agent is bound to a guardrail this layer does not manage, harden, or audit."
        ),
        path="agents/bedrock/terraform/agent.tf",
        old="guardrail_identifier = aws_bedrock_guardrail.this.guardrail_id",
        new='guardrail_identifier = "gr1a2b3c4d5e6"',
        gate="tests/agents/bedrock/guardrails/test_guardrail_attachment.py",
        must_fail="test_agent_binds_a_guardrail_that_exists",
    ),
    Attack(
        name="guardrail-bound-to-draft",
        rationale=(
            "DRAFT mutates in place, so no past verdict can be tied to the policy that "
            "produced it — the record-keeping obligation (AI Act Art. 12) fails silently."
        ),
        path="agents/bedrock/terraform/agent.tf",
        old="guardrail_version    = aws_bedrock_guardrail_version.this.version",
        new='guardrail_version    = "DRAFT"',
        gate="tests/agents/bedrock/guardrails/test_guardrail_attachment.py",
        must_fail="test_agent_binds_an_immutable_version_not_draft",
    ),
    Attack(
        name="prompt-attack-detuned",
        rationale=(
            "The filter stays declared — the old grep test still passed — but is switched "
            "off, while the red-team report keeps claiming prompt injection is covered."
        ),
        path="agents/bedrock/terraform/guardrail.tf",
        old="""      type            = "PROMPT_ATTACK"
      input_strength  = "HIGH\"""",
        new="""      type            = "PROMPT_ATTACK"
      input_strength  = "NONE\"""",
        gate="tests/agents/bedrock/guardrails/test_guardrail_attachment.py",
        must_fail="test_prompt_attack_filter_is_declared_and_active_on_input",
    ),
    Attack(
        name="pii-action-neutered",
        rationale=(
            "The PII entity is still declared, so the dataset card still promises "
            "anonymisation — but the action is a no-op and names flow through verbatim."
        ),
        path="agents/bedrock/terraform/guardrail.tf",
        old="""      type   = "NAME"
      action = "ANONYMIZE\"""",
        new="""      type   = "NAME"
      action = "NONE\"""",
        gate="tests/agents/bedrock/guardrails/test_guardrail_attachment.py",
        must_fail="test_pii_entities_match_the_policy_model_and_are_anonymised",
    ),
    Attack(
        name="pii-entity-removed",
        rationale=(
            "Terraform drops an entity while policy.py keeps modelling it, so the "
            "generated governance docs describe redaction that no longer happens."
        ),
        path="agents/bedrock/terraform/guardrail.tf",
        old="""    pii_entities_config {
      type   = "EMAIL"
      action = "ANONYMIZE"
    }
""",
        new="",
        gate="tests/agents/bedrock/guardrails/test_guardrail_attachment.py",
        must_fail="test_pii_entities_match_the_policy_model_and_are_anonymised",
    ),
    Attack(
        name="grounding-threshold-drift",
        rationale=(
            "The deployed grounding check is detuned to near-zero while policy.py and the "
            "AI-Act document keep publishing 0.75 as the control in force."
        ),
        path="agents/bedrock/terraform/guardrail.tf",
        old="""      type      = "GROUNDING"
      threshold = 0.75""",
        new="""      type      = "GROUNDING"
      threshold = 0.01""",
        gate="tests/agents/bedrock/guardrails/test_guardrail_attachment.py",
        must_fail="test_grounding_thresholds_match_the_policy_model",
    ),
    Attack(
        name="denied-topic-removed",
        rationale=(
            "The denied topic is deleted from Terraform; policy.py still denies it "
            "offline, so the red-team out-of-scope score describes nothing deployed."
        ),
        path="agents/bedrock/terraform/guardrail.tf",
        old='      name       = "investment-advice"',
        new='      name       = "investment-advice-DISABLED"',
        gate="tests/agents/bedrock/guardrails/test_guardrail_attachment.py",
        must_fail="test_denied_topics_match_the_policy_model",
    ),
    # --- the copilot layer --------------------------------------------------- #
    Attack(
        name="tool-schema-contradicts-implementation",
        rationale=(
            "get_fraud_score declared {transaction_id, card_hash} while its implementation "
            "took a feature vector — the tool the LLM is told to call could not be called "
            "with what it is told to send, and the old test 'matched contracts' by "
            "re-asserting the same literals in both files."
        ),
        path="agents/databricks/tools/get_fraud_score.py",
        old="    def get_fraud_score(self, transaction_id: str, card_hash: str) -> dict[str, Any]:",
        new="    def get_fraud_score(self, features: dict[str, Any]) -> dict[str, Any]:",
        gate="tests/agents/databricks/test_agent.py",
        must_fail="test_get_fraud_score_takes_ids_not_a_feature_vector",
    ),
    Attack(
        name="similar-case-search-crashes-on-a-score-column",
        rationale=(
            "Databricks appends a similarity score the manifest may not list; strict=True "
            "turned that into an uncaught ValueError in the analyst's tool call, and the "
            "fixture hand-built the manifest so the crash was unreachable in tests."
        ),
        path="agents/databricks/tools/search_similar_cases.py",
        old="        formatted.append(dict(zip(names, row, strict=False)))",
        new="        formatted.append(dict(zip(columns, row, strict=True)))",
        gate="tests/agents/databricks/test_search_similar_cases.py",
        must_fail="test_format_results_survives_an_extra_score_column",
    ),
    Attack(
        name="keyword-router-scored-in-sample-only",
        rationale=(
            "The router's cues were written from eval_dataset(), so scoring it there reports "
            "1.00 and means nothing; held-out it is 0.38, barely above chance. Scoring "
            "in-sample is the closed loop reporting itself."
        ),
        path="tests/agents/databricks/test_eval.py",
        old="    held_out = score_tool_selection(held_out_dataset(), keyword_router).accuracy",
        new="    held_out = score_tool_selection(eval_dataset(), keyword_router).accuracy",
        gate="tests/agents/databricks/test_eval.py",
        must_fail="test_the_keyword_baseline_is_a_floor_not_a_router",
    ),
    # --- the data-quality gates ---------------------------------------------- #
    Attack(
        name="dq-expectations-on-prefiltered-rows",
        rationale=(
            "What shipped: @dlt.expect_all on a frame select_valid had already filtered. "
            "The data-quality dashboard read 100% pass permanently, by construction — it "
            "would have read 100% during a total upstream corruption event."
        ),
        path="pipelines/silver/silver_pipeline.py",
        # Indented under `if STREAMING_ENABLED:` since the stream lineage was gated — the
        # body sits at 8 spaces now, and this attack must match that or it goes STALE.
        old=(
            "    def transactions_gated() -> DataFrame:\n"
            "        # Unfiltered on purpose: a row that fails a gate must still BE here, or"
            " the expectation\n"
            "        # below has nothing to fail on.\n"
            "        return silver_transforms.cleanse_transactions("
            'dlt.read_stream("bronze.transactions_stream"))'
        ),
        new=(
            "    def transactions_gated() -> DataFrame:\n"
            "        return silver_transforms.select_valid(\n"
            "            silver_transforms.cleanse_transactions("
            'dlt.read_stream("bronze.transactions_stream"))\n'
            "        )"
        ),
        gate="tests/pipelines/test_dq_expectations.py",
        must_fail="test_the_dq_metric_can_be_less_than_one_hundred_percent",
    ),
    # --- the self-healing agent ---------------------------------------------- #
    Attack(
        name="medic-promotes-staging-to-production",
        rationale=(
            "The most dangerous bug that shipped: a p99 blip promoted the newest STAGING "
            "version — which had failed its AUC gate — into the payment path, archiving "
            "the good model on the way past. Autonomously, with no human involved."
        ),
        path="agents/langgraph/medic.py",
        # Restores BOTH lines of the original. Reverting only the stage filter left the
        # version-number filter standing, which still excluded the newer Staging version —
        # so the attack proved nothing and gate_proof reported LEAKED. The defence is two
        # layers deep; a faithful attack has to remove the layer that shipped.
        old=(
            '    archived = [v for v in versions if v.current_stage == "Archived"]\n'
            "    if current is not None:\n"
            "        archived = [v for v in archived if int(v.version) < int(current.version)]"
        ),
        new='    archived = [v for v in versions if v.current_stage != "Production"]',
        gate="tests/agents/langgraph/test_medic.py",
        must_fail="test_rollback_never_promotes_a_staging_version",
    ),
    Attack(
        name="medic-rollback-skips-the-promotion-gate",
        rationale=(
            "A rollback is a promotion. Without the gate the agent becomes an exception to "
            "the AUC >= 0.92 policy the project calls non-negotiable."
        ),
        path="agents/langgraph/medic.py",
        old=(
            "    decision = _promotion_decision(mlflow_client, previous)\n"
            "    if not decision.promote:"
        ),
        new=("    decision = _promotion_decision(mlflow_client, previous)\n    if False:"),
        gate="tests/agents/langgraph/test_medic.py",
        must_fail="test_rollback_applies_the_same_promotion_gate_as_a_forward_promotion",
    ),
    Attack(
        name="checkpointer-silently-non-durable",
        rationale=(
            "`build_checkpointer` promised to 'fail loud rather than fall back to an "
            "in-memory saver' and the next line returned InMemorySaver() — so the retry "
            "bound and the action budget were per-PROCESS, and a crash handed the agent a "
            "fresh budget."
        ),
        path="agents/langgraph/graph.py",
        old="    if config.checkpoint_db:",
        new="    if False:",
        gate="tests/agents/langgraph/test_graph.py",
        must_fail="test_the_action_budget_survives_a_process_restart",
    ),
    Attack(
        name="refused-rollback-pages-nobody",
        rationale=(
            "A refusal is the agent saying 'I cannot fix this' while the endpoint is still "
            "broken — and it said it to nobody: rollback_refused never escalated."
        ),
        path="agents/langgraph/medic.py",
        old='        if result["action"] in _REFUSALS:',
        new="        if False:",
        gate="tests/agents/langgraph/test_medic.py",
        must_fail="test_a_refused_rollback_pages_a_human",
    ),
    Attack(
        name="p99-fires-on-a-single-sample",
        rationale=(
            "What shipped: one latency reading triggered a MODEL ROLLBACK. Latency and "
            "model correctness are unrelated — a cold start would archive a good model."
        ),
        path="agents/langgraph/supervisor.py",
        old="    return consecutive >= config.p99_confirmations_required, consecutive",
        new="    return True, consecutive",
        gate="tests/agents/langgraph/test_supervisor.py",
        must_fail="test_a_single_p99_spike_is_not_an_incident",
    ),
    Attack(
        name="medic-has-no-blast-radius-cap",
        rationale=(
            "The per-fingerprint retry counter bounds ONE incident; without a total cap, N "
            "failing pipelines each get their own budget and nothing bounds the agent."
        ),
        path="agents/langgraph/medic.py",
        old="    if spent < config.max_total_actions:\n        return None",
        new="    return None",
        gate="tests/agents/langgraph/test_graph.py",
        must_fail="test_the_agent_stops_acting_once_its_budget_is_spent",
    ),
    Attack(
        name="guardrail-block-only-counts",
        rationale=(
            "What shipped: a guardrail block incremented a Prometheus counter and the "
            "verdict shipped anyway — a verdict identified as leaking a card number was "
            "returned to the caller and written verbatim into the audit log, with the "
            "guardrail's own finding attached as metadata."
        ),
        path="ml/serving/stream_service.py",
        old="        released = gate.accepted and not guard.blocked",
        new="        released = True",
        gate="tests/serving/test_local_runtime.py",
        must_fail="test_a_guardrail_block_withholds_the_verdict",
    ),
    Attack(
        name="pii-refusal-crashes-the-funnel",
        rationale=(
            "The PII refusal was a poison pill: DecisionLogError escaped the consumer loop, "
            "the offset was never committed, and the restart re-read the same message. The "
            "trigger condition for a security control was a payment-scoring outage."
        ),
        path="ml/serving/stream_service.py",
        old="    except (DecisionLogError, OSError):",
        new="    except _NeverRaisedError:",
        gate="tests/serving/test_local_runtime.py",
        must_fail="test_a_refused_decision_record_does_not_take_the_funnel_down",
    ),
    # --- the verdict gate ---------------------------------------------------- #
    Attack(
        name="grounding-by-substring",
        rationale=(
            "What shipped: `citation in ref or ref in citation`. A fabricated article "
            "appended to a real one was ACCEPTED, because the real one is a substring of "
            "the pair — the exact threat the grounding check exists to stop."
        ),
        path="agents/bedrock/eval/judge.py",
        old="    cited = provision_pairs(citation)\n    if not cited:\n        return set(), False",
        new=(
            "    if any(citation.lower() in r.lower() or r.lower() in citation.lower()"
            " for r in context_refs):\n        return set(), True\n"
            "    cited = provision_pairs(citation)\n    if not cited:\n        return set(), False"
        ),
        gate="tests/agents/bedrock/eval/test_verdict_gate.py",
        must_fail="test_labeled_set_matches_expected_outcomes",
    ),
    Attack(
        name="grounding-loses-the-instrument-article-pairing",
        rationale=(
            "The bypass that defeated the FIRST grounding fix. A flat token set unions the "
            "context, so 'AMLD5 Art. 97' — a provision that does not exist — is accepted "
            "because both halves appear somewhere in it."
        ),
        path="agents/bedrock/eval/judge.py",
        old="    cited = provision_pairs(citation)",
        new="    cited = {(p, p) for p in provisions(citation)}",
        gate="tests/agents/bedrock/eval/test_verdict_gate.py",
        must_fail="test_labeled_set_matches_expected_outcomes",
    ),
    Attack(
        name="grounding-ignores-the-reasoning",
        rationale=(
            "Grounding checked only `regulatory_reference`, so an invented article in the "
            "prose — the field a human actually reads — shipped with a clean citation."
        ),
        path="agents/bedrock/eval/judge.py",
        old=(
            "    fabricated |= {\n"
            '        f"{i} {a}"\n'
            "        for i, a in provision_pairs(reasoning)"
            " - _retrieved_pairs(context.retrieved_references)\n"
            "    }"
        ),
        new="    pass",
        gate="tests/agents/bedrock/eval/test_verdict_gate.py",
        must_fail="test_labeled_set_matches_expected_outcomes",
    ),
    Attack(
        name="decision-softening-allowed",
        rationale=(
            "What shipped: the escalation cue list applied symmetrically. The model said "
            "block, the agent said allow, and the word 'however' was the justification."
        ),
        path="agents/bedrock/eval/judge.py",
        # Restores the OLD rule exactly: no direction check, and "however" back in the cue
        # list. Removing only the direction check is not the old bug — the verdict still
        # trips the cue check, so the gate goes red for the wrong reason and the attack
        # proves nothing. gate_proof caught that: it reported LEAKED until this was
        # faithful to what actually shipped.
        old=(
            "    if _CAUTION[action] < _CAUTION[hint]:\n"
            '        # The direction that used to be waved through on the word "however".\n'
        ),
        new="    if False:  # noqa: SIM108\n",
        gate="tests/agents/bedrock/eval/test_verdict_gate.py",
        must_fail="test_labeled_set_matches_expected_outcomes",
    ),
    Attack(
        name="faithfulness-back-to-prose-only",
        rationale=(
            "What shipped: faithfulness scanned prose for 15 English phrases, so two "
            "invented drivers in one sentence produced zero invented drivers."
        ),
        path="agents/bedrock/eval/judge.py",
        old="    invented = sorted(set(declared) - top)",
        new="    invented = []",
        gate="tests/agents/bedrock/eval/test_verdict_gate.py",
        must_fail="test_labeled_set_matches_expected_outcomes",
    ),
    Attack(
        name="pii-pattern-matches-floats-again",
        rationale=(
            "The card-number pattern matched the mantissa of a float, so every verdict "
            "quoting a computed feature was a 'PII leak' — a control that blocks correct "
            "output, with a published false-positive rate."
        ),
        path="agents/bedrock/pii.py",
        old=(
            "_CARD_CANDIDATE = re.compile("
            r'r"(?<![-\d.A-Za-z])(?:\d[ -]?){13,19}(?![A-Za-z])(?!\.?\d)")'
        ),
        new=r'_CARD_CANDIDATE = re.compile(r"\b(?:\d[ -]?){13,19}\b")',
        gate="tests/agents/bedrock/test_pii.py",
        # NOT test_arithmetic_is_not_pii: its three literal floats happen to fail Luhn, so
        # they pass even with the boundary rule gone. gate_proof reported LEAKED until the
        # gate was a property test over many values — where ~10% of mantissas slip through
        # Luhn and the rule proves necessary.
        must_fail="test_no_computed_feature_value_is_ever_a_card_number",
    ),
    Attack(
        name="pii-detector-drops-the-grouping-rule",
        rationale=(
            "Without it a UUID's 8-4-4 hex split reads as a card grouping, so a generated "
            "correlation id refuses ~1 audit record in 10,000 at random — and the mask that "
            "used to hide that also hid a PAN formatted as a UUID."
        ),
        path="agents/bedrock/pii.py",
        old="    return _grouping_is_card_like(candidate) and _luhn_valid(digits)",
        new="    return _luhn_valid(digits)",
        gate="tests/agents/bedrock/test_pii.py",
        must_fail="test_no_uuid_is_ever_a_card_number",
    ),
    Attack(
        name="guardrail-runs-its-own-pii-detector",
        rationale=(
            "policy.py imported PII_PATTERNS and re-ran the regex itself, so it kept the "
            "pre-Luhn detector while the gate and the log ran the fixed one — 'one "
            "definition, used by every control' was one definition and three behaviours."
        ),
        path="agents/bedrock/guardrails/policy.py",
        old="        if self.pii_entities and contains_pii(text):",
        new='        if self.pii_entities and _matches_any(text, (r"\\b(?:\\d[ -]?){13,19}\\b",)):',
        gate="tests/agents/bedrock/test_pii.py",
        must_fail="test_the_guardrail_runs_the_same_detector_as_the_gate_and_the_log",
    ),
    Attack(
        name="pii-detector-drops-the-luhn-check",
        rationale=(
            "Without Luhn the detector means 'a lot of digits', not 'a card number'. "
            "gate_proof found this: a random UUID contains a 15-digit hyphenated run, so "
            "the audit log refused to record roughly one decision in ten thousand, at "
            "random — a PII detector with a coin-flip false-positive rate."
        ),
        path="agents/bedrock/pii.py",
        old="    return _grouping_is_card_like(candidate) and _luhn_valid(digits)",
        new="    return _grouping_is_card_like(candidate)",
        gate="tests/agents/bedrock/test_pii.py",
        must_fail="test_a_digit_run_that_fails_the_luhn_checksum_is_not_a_card",
    ),
    Attack(
        name="decision-log-drops-the-model-version",
        rationale=(
            "Without it, 'which model decided this transaction?' has no answer for any "
            "decision the system ever made, and the model card documents a model that "
            "cannot be tied to its own outputs."
        ),
        path="ml/serving/stream_service.py",
        old='        model_version=str(scored["model_version"]),',
        new='        model_version="",',
        gate="tests/agents/bedrock/eval/test_decision_log.py",
        must_fail="test_the_record_carries_the_model_version_that_made_the_decision",
    ),
    Attack(
        name="decision-log-only-records-flagged-cases",
        rationale=(
            "The old stdout log line recorded flagged cases only. AI Act Art. 12 is about "
            "every inference, and the ~99% that are approved are the ones a dispute is "
            "about."
        ),
        path="ml/serving/stream_service.py",
        old="    if decisions is not None:",
        new="    if decisions is not None and tier2:",
        gate="tests/agents/bedrock/eval/test_decision_log.py",
        must_fail="test_every_scored_transaction_is_recorded",
    ),
    Attack(
        name="grounding-score-hardcoded-to-one",
        rationale=(
            "What shipped: `evaluate_output(..., grounding_score=1.0)`. The GROUNDING "
            "policy class could never fire in the only path that runs it."
        ),
        path="ml/serving/stream_service.py",
        old=(
            "    cited = provision_pairs(reasoning)\n"
            "    if not cited:\n"
            "        # Reasoning that cites nothing is grounded in nothing.\n"
            "        return 0.0"
        ),
        new="    return 1.0",
        gate="tests/serving/test_local_runtime.py",
        must_fail="test_grounding_is_measured_not_asserted",
    ),
    # --- the deploy path ----------------------------------------------------- #
    Attack(
        name="deploy-static-aws-keys",
        rationale=(
            "What shipped: non-rotating IAM user keys with near-admin rights, living "
            "indefinitely in GitHub secrets. The old test only looked for a literal AKIA "
            "string, so it blessed the pattern instead of catching it."
        ),
        path=".github/workflows/deploy.yml",
        old=(
            "          role-to-assume: ${{ secrets.AWS_DEPLOY_ROLE_ARN }}\n"
            "          role-session-name: fintelliguard-plan"
        ),
        new=(
            "          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}\n"
            "          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}"
        ),
        gate="tests/cicd/test_workflows.py",
        must_fail="test_cloud_workflows_use_oidc_not_long_lived_keys",
    ),
    Attack(
        name="deploy-applies-without-a-plan",
        rationale=(
            "What shipped: three consecutive `apply -auto-approve` and no plan step, in a "
            "repo whose rules say never apply without reviewing the plan."
        ),
        path=".github/workflows/deploy.yml",
        old='terraform -chdir="${dir}" apply -input=false tfplan',
        new='terraform -chdir="${dir}" apply -auto-approve -input=false',
        gate="tests/cicd/test_workflows.py",
        must_fail="test_deploy_plans_before_it_applies",
    ),
    Attack(
        name="deploy-skips-ci",
        rationale=(
            "Deploy had no dependency on CI and no branch filter, so lint-failing, "
            "red-team-failing code could be applied straight to the cloud."
        ),
        path=".github/workflows/deploy.yml",
        # Targets the dependency itself, not the job's name — the name changed when the
        # deploy was restructured and the attack went STALE, which is the rule working:
        # a mutation whose target has moved is silently testing nothing.
        old="    needs: validate\n    runs-on: ubuntu-latest",
        new="    runs-on: ubuntu-latest",
        gate="tests/cicd/test_workflows.py",
        must_fail="test_deploy_only_ships_what_ci_validated",
    ),
    Attack(
        name="deploy-offers-a-phantom-prod",
        rationale=(
            "What shipped: `prod` in the dropdown with no prod target in the bundle, so "
            "the deploy failed at step 4 AFTER three layers had already applied."
        ),
        path=".github/workflows/deploy.yml",
        old="        options: [dev]\n        default: dev",
        new="        options: [dev, prod]\n        default: dev",
        gate="tests/cicd/test_workflows.py",
        must_fail="test_every_offered_environment_has_a_bundle_target",
    ),
    Attack(
        name="guardrail-detuned-via-a-local",
        rationale=(
            "The bypass that defeated the FIRST fix. A blacklist of off-states cannot see "
            "`input_strength = local.pa_strength` — the parser returns the unevaluated "
            "expression, which is not in the blacklist, so the filter reads as active while "
            "being off. Seven tests passed with PROMPT_ATTACK and PII redaction disabled."
        ),
        path="agents/bedrock/terraform/guardrail.tf",
        old='      type            = "PROMPT_ATTACK"\n      input_strength  = "HIGH"',
        new=('      type            = "PROMPT_ATTACK"\n      input_strength  = local.pa_strength'),
        gate="tests/agents/bedrock/guardrails/test_guardrail_attachment.py",
        must_fail="test_prompt_attack_filter_is_declared_and_active_on_input",
    ),
    Attack(
        name="kb-delete-index-without-a-wildcard",
        rationale=(
            "The bypass that defeated the FIRST fix. `aoss:DeleteIndex` destroys the corpus "
            "every verdict is grounded in and has no trailing star, so a blacklist on `*` "
            "waves it through — it blacklisted the syntax of the finding, not the capability."
        ),
        path="agents/bedrock/terraform/knowledge_base.tf",
        old='"aoss:ReadDocument", "aoss:WriteDocument"',
        new='"aoss:ReadDocument", "aoss:WriteDocument", "aoss:DeleteIndex"',
        gate="tests/agents/bedrock/test_knowledge_base_security.py",
        must_fail="test_the_kb_role_holds_only_the_permissions_ingestion_needs",
    ),
    Attack(
        name="databricks-task-exits-cleanly-and-is-reported-failed",
        rationale=(
            "`sys.exit(0)` looks like the most ordinary line in Python. Inside Databricks' "
            "notebook-like task host it raises SystemExit as an EXCEPTION, so a job that "
            "wrote its table correctly is reported INTERNAL_ERROR — 11 minutes into a deploy, "
            "with 'SystemExit: 0' as the only clue that nothing was actually wrong."
        ),
        path="infra/bundles/prereq/seed_resolved_cases.py",
        old="    main()",
        new="    sys.exit(main())",
        gate="tests/bundles/test_databricks_tasks.py",
        must_fail="test_a_task_script_never_calls_sys_exit",
    ),
    Attack(
        name="dlt-pipeline-drops-the-executor-wheel",
        rationale=(
            "The gold transforms run per-card logic through Spark applyInPandas, which "
            "serializes those functions to WORKER processes. The driver-side sys.path "
            "bootstrap does not reach a worker, so without the wheel installed cluster-wide "
            "the workers fail with ModuleNotFoundError: No module named 'pipelines'. "
            "Removing the wheel library restores that failure."
        ),
        path="infra/bundles/resources/pipelines.yml",
        old="          - ../../../dist/fintelliguard-0.0.0-py3-none-any.whl\n",
        new="",
        gate="tests/bundles/test_databricks_tasks.py",
        must_fail="test_the_dlt_pipeline_installs_the_repo_wheel",
    ),
    Attack(
        name="kafka-stream-cannot-be-disabled-for-a-batch-run",
        rationale=(
            "The Kafka stream lineage and the IEEE batch lineage share one pipeline. A Kafka "
            "source with no bootstrap servers cannot even be ANALYZED, so its declaration "
            "failed the whole pipeline — including the batch tables the training job needs. "
            "The stream lineage must be gated so a batch-only run omits it. Removing the "
            "guard puts the un-analyzable Kafka table back into every run."
        ),
        path="pipelines/bronze/bronze_pipeline.py",
        old='if STREAMING_ENABLED:\n\n    @dlt.table(\n        name="bronze.transactions_stream",',
        new='if True:\n\n    @dlt.table(\n        name="bronze.transactions_stream",',
        gate="tests/pipelines/test_streaming_guard.py",
        must_fail="test_streaming_off_registers_only_the_batch_lineage",
    ),
    Attack(
        name="dlt-view-with-a-schema-prefix",
        rationale=(
            "A DLT view is session-scoped and DLT rejects a multipart name on it — "
            "'View with multipart name gold.X is not supported'. All four gated views "
            "shipped with a prefix and it surfaced only on the pipeline's first real run, "
            "because the local tests register decorators through a stub that accepts any "
            "name. The one rule DLT enforces was the one rule nothing checked."
        ),
        path="pipelines/gold/gold_pipeline.py",
        old='    name="txn_features_realtime_gated",',
        new='    name="gold.txn_features_realtime_gated",',
        gate="tests/pipelines/test_dlt_naming.py",
        must_fail="test_no_dlt_view_carries_a_schema_prefix",
    ),
    Attack(
        name="dlt-source-uses-a-relative-import",
        rationale=(
            "All three medallion pipelines opened with `from . import <layer>_transforms`. "
            "Correct Python, impossible in DLT: it executes each source like a notebook "
            "cell, with no parent package, so the pipeline dies at start with "
            "'attempted relative import with no known parent package'. It hid for the whole "
            "life of the project because the unit tests import these files AS a package, "
            "which is the one context that cannot reproduce it."
        ),
        path="pipelines/bronze/bronze_pipeline.py",
        old="from pipelines.bronze import bronze_transforms  # noqa: E402",
        new="from . import bronze_transforms  # noqa: E402",
        gate="tests/bundles/test_databricks_tasks.py",
        must_fail="test_a_dlt_source_uses_absolute_imports",
    ),
    Attack(
        name="case-index-source-without-change-data-feed",
        rationale=(
            "A DELTA_SYNC index stays current by reading the source table's change data "
            "feed, and Databricks refuses to create one without it. The seed created the "
            "table and never set the property, so the index failed with a message about "
            "the SOURCE TABLE during a step that reads as a vector-search problem."
        ),
        path="infra/bundles/prereq/seed_resolved_cases.py",
        old=(
            '    spark.sql(f"ALTER TABLE {fqn} SET TBLPROPERTIES '
            '(delta.enableChangeDataFeed = true)")'
        ),
        new="    pass  # change data feed left off",
        gate="tests/bundles/test_databricks_tasks.py",
        must_fail="test_the_case_index_source_table_enables_change_data_feed",
    ),
    Attack(
        name="serving-deploys-the-model-the-gate-rejected",
        rationale=(
            "The promotion gate's entire value is that a model below AUC-ROC 0.92 or fraud "
            "precision 0.85 is not served. But a rejected model IS still registered — it "
            "just never takes the `production` alias. So resolving the served version as "
            "'the latest one' deploys exactly the model the gate refused, while the gate's "
            "own log line still reads REJECT. The gate would be decorative and look active."
        ),
        path=".github/workflows/deploy.yml",
        old='if ! version="$(databricks model-versions get-by-alias \\',
        new='if ! version="$(databricks model-versions list \\',
        gate="tests/bundles/test_databricks_tasks.py",
        must_fail="test_serving_is_pinned_to_the_promoted_version_not_the_latest",
    ),
    Attack(
        name="vector-index-races-its-own-endpoint",
        rationale=(
            "Declaring the endpoint next to the index reads as tidy and cannot work: DAB "
            "deploys a bundle's resources in one pass with no ordering, and a Vector Search "
            "endpoint provisions for minutes. The index is then created against an endpoint "
            "that exists only as an accepted API call — NOT_FOUND, which reads like a naming "
            "or permissions fault and is neither."
        ),
        path="infra/bundles/resources/vector_search.yml",
        old="resources:\n  vector_search_indexes:",
        new=(
            "resources:\n"
            "  vector_search_endpoints:\n"
            "    cases:\n"
            "      name: ${var.vector_endpoint_name}\n"
            "      endpoint_type: STANDARD\n\n"
            "  vector_search_indexes:"
        ),
        gate="tests/bundles/test_databricks_tasks.py",
        must_fail="test_the_vector_endpoint_is_created_before_the_index_that_needs_it",
    ),
    Attack(
        name="corpus-uploaded-around-the-screen",
        rationale=(
            "What shipped: the ONLY documented way to load the corpus was a manual "
            "`aws s3 cp --recursive` that no CI step ran and that never calls "
            "screen_document(). The KB was found ACTIVE and empty. Meanwhile the EU-AI-Act "
            "document states as regulated fact that the corpus is screened at ingestion — a "
            "control that existed, was tested, was attacked here, and sat on the wrong side "
            "of the door. This restores the bypass."
        ),
        path=".github/workflows/deploy.yml",
        old="          python -m agents.bedrock.kb.ingest \\",
        new='          aws s3 cp agents/bedrock/kb/corpus/ "s3://${bucket}" --recursive \\',
        gate="tests/agents/bedrock/test_kb_ingest.py",
        must_fail="test_the_deploy_loads_the_corpus_through_the_screening_module",
    ),
    Attack(
        name="secret-purge-reaches-live-secrets",
        rationale=(
            "The purge step exists because a secret scheduled for deletion still owns its "
            "name and blocks re-creation. Dropping the `DeletedDate` filter turns it from "
            "'clean up what teardown left in limbo' into 'force-delete every secret under "
            "this prefix', irreversibly and with no recovery window — while the step name "
            "still says 'pending deletion'."
        ),
        path=".github/workflows/deploy.yml",
        old="SecretList[?DeletedDate!=null && starts_with(Name, '${PREFIX}')].Name",
        new="SecretList[?starts_with(Name, '${PREFIX}')].Name",
        gate="tests/cicd/test_workflows.py",
        must_fail="test_the_secret_purge_can_only_touch_secrets_already_being_deleted",
    ),
    Attack(
        name="teardown-stops-at-the-first-failing-layer",
        rationale=(
            "What shipped: the destroy layers ran with the default `if: success()`. Layer 3 "
            "failed on two non-empty versioned buckets, so layer 4 never ran and left the "
            "whole infra/aws layer — MSK, VPC, NAT — ACTIVE and billing, on a run that "
            "reported failure having destroyed nothing below the failure point. The layer "
            "that costs the most per hour was protected by the least."
        ),
        path=".github/workflows/destroy.yml",
        # Strips `always()` from every layer's condition. What remains still reads like a
        # careful guard — the confirmation and credential checks are untouched — which is
        # exactly why the original went unnoticed.
        old="        if: always() && steps.guard.outcome == 'success'",
        new="        if: steps.guard.outcome == 'success'",
        gate="tests/cicd/test_workflows.py",
        must_fail="test_destroy_continues_to_later_layers_when_one_fails",
    ),
    Attack(
        name="teardown-forgets-the-delete-markers",
        rationale=(
            "S3 will not delete a bucket holding delete markers, and a bucket holding ONLY "
            "markers looks empty to `aws s3 ls`. Emptying just `Versions` therefore fails "
            "with BucketNotEmpty against a bucket that every casual check calls empty — and "
            "`force_destroy` cannot save it, because destroy reads that flag from state."
        ),
        path="scripts/empty_layer_buckets.sh",
        old="[(.Versions // [])[], (.DeleteMarkers // [])[] | {Key, VersionId}]",
        new="[(.Versions // [])[] | {Key, VersionId}]",
        gate="tests/cicd/test_workflows.py",
        must_fail="test_the_bucket_emptying_handles_delete_markers_and_pagination",
    ),
    Attack(
        name="ml-steps-run-on-an-expired-oidc-session",
        rationale=(
            "The OIDC role session is one hour, and the deploy runs longer: MSK alone takes "
            "~26 minutes, then the workspace, corpus, seed cluster and bundle. By the ML "
            "steps the credentials assumed at job start have expired — ExpiredToken. It was "
            "invisible until a run first got past 4b. Removing the refresh restores it."
        ),
        path=".github/workflows/deploy.yml",
        # Neuter the refresh step's role assumption — the faithful mutation is the step being
        # absent, and the session name makes this block unique to it.
        old=(
            "        uses: aws-actions/configure-aws-credentials"
            "@7474bc4690e29a8392af63c5b98e7449536d5c3a # v4.3.1\n"
            "        with:\n"
            "          aws-region: ${{ env.AWS_REGION }}\n"
            "          role-to-assume: ${{ secrets.AWS_DEPLOY_ROLE_ARN }}\n"
            "          role-session-name: fintelliguard-apply-ml"
        ),
        new='        run: echo "refresh removed"',
        gate="tests/cicd/test_workflows.py",
        must_fail="test_the_deploy_refreshes_aws_credentials_before_the_long_ml_stretch",
    ),
    Attack(
        name="delete-batch-passed-as-a-command-line-argument",
        rationale=(
            '`jq -n --argjson o "$objects"` reads as ordinary shell and dies with '
            "'Argument list too long' once a page is large enough — a 1000-key page is past "
            "ARG_MAX. It survived the bucket holding one object and failed on the DBFS root, "
            "so the emptying broke on precisely the buckets that needed it, while the test "
            "asserting pagination existed stayed green."
        ),
        path="scripts/empty_layer_buckets.sh",
        old="    printf '%s' \"${raw}\" \\",
        new='    jq -n --argjson o "${raw}" \\',
        gate="tests/cicd/test_workflows.py",
        must_fail="test_the_bucket_emptying_handles_delete_markers_and_pagination",
    ),
    Attack(
        name="one-layer-destroyed-without-emptying-its-buckets",
        rationale=(
            "The emptying step existed for infra/databricks only — the layer that had "
            "failed. infra/aws holds 651 MB of IEEE-CIS data and agents/bedrock holds the "
            "regulatory corpus, neither declaring force_destroy, so both were one teardown "
            "away from the same BucketNotEmpty. Removing any one of the three restores that."
        ),
        path=".github/workflows/destroy.yml",
        old="        run: ./scripts/empty_layer_buckets.sh infra/aws",
        new="        run: echo 'skipped'",
        gate="tests/cicd/test_workflows.py",
        must_fail="test_every_layer_that_owns_buckets_is_emptied_before_it_is_destroyed",
    ),
    Attack(
        name="credentialed-job-trusts-a-movable-tag",
        rationale=(
            "What shipped: `aws-actions/configure-aws-credentials@v4` inside the job that "
            "holds `id-token: write`. The gate meant to stop this asserted only "
            '`not endswith(("@main", "@master"))` — it blacklisted two spellings instead of '
            "demanding immutability, so every tag-pinned action passed. A tag is a movable "
            "pointer: `git tag -f v4 && git push --force` re-aims it at new code with no "
            "diff in this repo, and that code runs holding credentials to the AWS estate."
        ),
        path=".github/workflows/deploy.yml",
        # Both occurrences (plan + apply job) revert; either one alone is the violation.
        old=(
            "aws-actions/configure-aws-credentials"
            "@7474bc4690e29a8392af63c5b98e7449536d5c3a # v4.3.1"
        ),
        new="aws-actions/configure-aws-credentials@v4",
        gate="tests/cicd/test_workflows.py",
        must_fail="test_no_workflow_pins_an_action_to_a_mutable_ref",
    ),
    Attack(
        name="kb-open-to-every-principal",
        rationale=(
            'Nothing checked WHO. `Principal = ["*"]` gives every principal in the '
            "account read/write on the AML/PSD2 corpus, and shipped green."
        ),
        path="agents/bedrock/terraform/knowledge_base.tf",
        # Targets ONE line inside the principal list, not the whole block. The block has
        # now grown twice (the deployer, then the index Lambda) and each time the old
        # whole-block target went STALE — an attack silently testing nothing, caught only
        # because the harness refuses to score a stale patch. Injecting a wildcard INTO the
        # list is the same violation and survives the list changing again.
        old="      aws_iam_role.kb.arn,",
        new='      "*",',
        gate="tests/agents/bedrock/test_knowledge_base_security.py",
        must_fail="test_the_kb_data_policy_names_a_principal_and_a_scoped_resource",
    ),
    Attack(
        name="kb-ingests-a-poisoned-document",
        rationale=(
            "Indirect prompt injection. The corpus was read straight into the vector store "
            "with no validation, while the system prompt tells the agent to ground every "
            "claim in retrieved text — so a document that INSTRUCTS is retrieved as "
            "authority. The guardrail already blocked that text; the ingester never asked."
        ),
        path="agents/bedrock/kb/chunking.py",
        old="        if reason := screen_document(text, policy):",
        new="        if False:",
        gate="tests/agents/bedrock/test_chunking.py",
        must_fail="test_a_poisoned_regulatory_document_is_refused_at_ingestion",
    ),
    # --- the knowledge base -------------------------------------------------- #
    Attack(
        name="kb-exposed-to-internet",
        rationale=(
            "What shipped: the vector store holding the AML/PSD2 corpus accepted public "
            "network connections, in a file whose header says 'private'. Invisible to a "
            "grep because the policy lives inside jsonencode()."
        ),
        path="agents/bedrock/terraform/knowledge_base.tf",
        old="    AllowFromPublic = false",
        new="    AllowFromPublic = true",
        gate="tests/agents/bedrock/test_knowledge_base_security.py",
        must_fail="test_the_regulatory_corpus_is_not_reachable_from_the_public_internet",
    ),
    Attack(
        name="kb-private-but-dangling",
        rationale=(
            "Hardened-looking and broken: AllowFromPublic=false pointing at a VPC endpoint "
            "that does not exist locks out the Knowledge Base itself."
        ),
        path="agents/bedrock/terraform/knowledge_base.tf",
        old="SourceVPCEs    = [aws_opensearchserverless_vpc_endpoint.kb.id]",
        new="SourceVPCEs    = [aws_opensearchserverless_vpc_endpoint.deleted.id]",
        gate="tests/agents/bedrock/test_knowledge_base_security.py",
        must_fail="test_the_private_network_rule_points_at_a_vpc_endpoint_that_exists",
    ),
    Attack(
        name="kb-wildcard-permissions",
        rationale=(
            "`aoss:*` on the data plane includes destroying the corpus every verdict is "
            "grounded in — and it sat under a comment promising no wildcards."
        ),
        path="agents/bedrock/terraform/knowledge_base.tf",
        old=(
            'Permission   = ["aoss:CreateIndex", "aoss:DescribeIndex", '
            '"aoss:UpdateIndex", "aoss:ReadDocument", "aoss:WriteDocument"]'
        ),
        new='Permission   = ["aoss:*"]',
        gate="tests/agents/bedrock/test_knowledge_base_security.py",
        must_fail="test_the_kb_role_holds_only_the_permissions_ingestion_needs",
    ),
    Attack(
        name="invariant-covered-in-name-only",
        rationale=(
            "The coverage test did the OPPOSITE of its purpose: it flagged only invariants "
            "FALSE on every row, which another test already catches harder. A trivially-true "
            "invariant — the thing it claimed to catch — passed silently."
        ),
        path="ml/features/semantics.py",
        old="        at_boundary=lambda f: f.txn_velocity_1h == MIN_WINDOW_COUNT,",
        new="        at_boundary=lambda f: False,",
        gate="tests/features/test_parity_distributional.py",
        must_fail="test_every_invariant_is_exercised_by_this_corpus",
    ),
    # --- the feature contract ------------------------------------------------ #
    Attack(
        name="velocity-off-by-one-restored",
        rationale=(
            "Drop the window-count floor and IEEE trains on a 'no activity' bucket the "
            "serving path cannot produce — every learned split shifts by one."
        ),
        path="ml/features/adapter_ieee.py",
        old='velocity_1h = max(int(_num(row, "C1")), MIN_WINDOW_COUNT)',
        new='velocity_1h = int(_num(row, "C1"))',
        gate="tests/features/test_adapter_ieee.py",
        must_fail="test_raw_zero_counts_are_floored_to_the_window_convention",
    ),
    Attack(
        name="amount-sum-proxy-reverted",
        rationale=(
            "The old C1*amount proxy returns 0.0 for a 120.0 transaction — a state the "
            "stream path, whose sum always contains the current amount, cannot reach."
        ),
        path="ml/features/adapter_ieee.py",
        old="amount_sum_1h = amount + (velocity_1h - MIN_WINDOW_COUNT) * ctx.amount_mean",
        new="amount_sum_1h = (velocity_1h - MIN_WINDOW_COUNT) * amount",
        gate="tests/features/test_parity_distributional.py",
        must_fail="test_both_adapters_satisfy_the_canonical_semantics",
    ),
    Attack(
        name="unusual-hour-linearised",
        rationale=(
            "Treating a circular quantity as linear: the [min,max] band swallows the whole "
            "night for any card active either side of midnight."
        ),
        path="ml/features/transforms.py",
        old="    return min(circular_hour_distance(hour, h) for h in seen) > HOUR_TOLERANCE",
        new="    return not (min(seen) <= hour <= max(seen))",
        gate="tests/features/test_transforms.py",
        must_fail="test_midnight_spanning_card_still_has_unusual_hours",
    ),
    Attack(
        name="device-seen-shadows-card-age",
        rationale=(
            "Restoring `card_age_days > 0` makes feature #9 a deterministic function of "
            "feature #8 — zero independent signal, and the new-device fraud archetype "
            "becomes undetectable by the feature designed to catch it."
        ),
        path="ml/features/adapter_ieee.py",
        old="        device_seen_before = device_txn_count_24h > MIN_WINDOW_COUNT",
        new="        device_seen_before = card_age_days > 0",
        gate="tests/features/test_adapter_ieee.py",
        must_fail="test_device_seen_before_falls_back_to_device_activity_not_card_age",
    ),
    Attack(
        name="nan-guard-removed",
        rationale=(
            "NaN is not NULL: silver's coalesce does not catch it, and int(NaN) raises, "
            "killing the whole card group's Spark task. IEEE-CIS's C/D columns are sparse."
        ),
        path="ml/features/adapter_ieee.py",
        old="    return default if number != number else number  # NaN != NaN",
        new="    return number",
        gate="tests/features/test_adapter_ieee.py",
        must_fail="test_nan_cells_are_treated_as_missing_not_crashes",
    ),
    Attack(
        name="redteam-signature-removed",
        rationale=(
            "The red-team set must gate the policy model, not decorate it: deleting a "
            "detector has to drop the block rate and fail CI."
        ),
        path="agents/bedrock/guardrails/policy.py",
        old=r'    r"ignore (all|any|the) (previous|prior|above) (instructions|prompts?)",',
        new="",
        gate="tests/agents/bedrock/guardrails/test_redteam_coverage.py",
        must_fail="test_full_block_rate_no_false_positives",
    ),
    Attack(
        name="feature-count-drift-in-governance-doc",
        rationale=(
            "The EU-AI-Act / model-card feature count must be DERIVED from the schema, not a "
            "prose literal the generator reproduces from itself. Drop a feature and the "
            "committed regulated docs have to go stale — the count and the input table both "
            "shrink — so `--check` fails CI instead of shipping a document that misstates the "
            "number of model inputs."
        ),
        path="ml/features/schema.py",
        old='    FeatureSpec("amount_log", float, minimum=0.0),',
        new="",
        gate="tests/ml/governance/test_generate.py",
        must_fail="test_committed_docs_are_up_to_date",
    ),
    Attack(
        name="synthetic-cases-stop-declaring-themselves",
        rationale=(
            "The Tier-3 case index is seeded with SYNTHETIC resolutions and can never be "
            "replaced by real ones — there are no real investigations to substitute. So the "
            "disclosure must open `case_text`, the column Vector Search embeds and returns. "
            "Demote it to a metadata column and the analyst asks 'have we seen this?', gets "
            "a confident answer, and has no way to know it was invented."
        ),
        path="agents/databricks/cases/seed.py",
        old="""            DISCLOSURE,
            "",
""",
        new="",
        gate="tests/agents/databricks/test_case_seed.py",
        must_fail="test_the_disclosure_survives_truncation_to_a_search_preview",
    ),
    Attack(
        name="seeded-cases-only-ever-confirm-the-model",
        rationale=(
            "A case corpus where every outcome is confirmed fraud can only ever agree with "
            "the flag. The value of 'have we seen this?' is the RATIO — four were fraud, one "
            "was a traveller — so an all-fraud fixture silently turns the analyst's "
            "independent check into an echo of the model."
        ),
        path="agents/databricks/cases/seed.py",
        old="        is_fraud = rng.random() < _FRAUD_RATE[archetype]",
        new="        is_fraud = True",
        gate="tests/agents/databricks/test_case_seed.py",
        must_fail="test_every_archetype_carries_both_outcomes",
    ),
)


# --------------------------------------------------------------------------- #
# Running a gate
# --------------------------------------------------------------------------- #


def _stage(dest: Path) -> None:
    """Copy the repo subset a gate needs into `dest`."""
    for item in COPY:
        src = REPO / item
        if not src.exists():
            raise SystemExit(f"cannot stage: {src} is missing")
        if src.is_dir():
            shutil.copytree(
                src,
                dest / item,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".terraform"),
            )
        else:
            shutil.copy2(src, dest / item)


def _run_gate(workdir: Path, gate: str) -> subprocess.CompletedProcess[str]:
    """Run the real gate against the staged copy.

    PYTHONPATH puts the staged copy ahead of the editable install, so the gate reads the
    mutated files and not the developer's working tree.
    """
    return subprocess.run(
        [sys.executable, "-m", "pytest", gate, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=workdir,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(workdir), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
    )


def _finding_lines(output: str) -> list[str]:
    """Lines that only appear when pytest reports a failure.

    Without this filter an attack could score green off the gate's own clean output — the
    marker appears in a `collected 7 items` line whether or not anything was caught.
    """
    lines = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped.startswith(("FAILED", "ERROR")):
            continue
        if any(token in stripped for token in _NOT_A_FINDING):
            continue
        lines.append(stripped)
    return lines


def _blocked_for_the_right_reason(result, attack: Attack) -> tuple[bool, str]:
    """Did the gate refuse THIS violation, or just happen to be unhappy?"""
    if result.returncode == 0:
        return False, "gate passed — the violation went through"

    findings = _finding_lines(result.stdout + result.stderr)
    if not findings:
        return False, "gate exited non-zero but reported no failing test (crash? bad path?)"

    hits = [ln for ln in findings if f"::{attack.must_fail}" in ln]
    if not hits:
        return False, (
            f"gate went red, but not via {attack.must_fail} — it failed for an unrelated "
            f"reason: {findings[0][:120]}"
        )
    if hits[0].startswith("ERROR"):
        return False, f"{attack.must_fail} errored rather than asserted: {hits[0][:120]}"
    return True, hits[0][:150]


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def _baseline(gate: str, verbose: bool) -> tuple[bool, str]:
    """A gate must be GREEN before an attack on it means anything."""
    with tempfile.TemporaryDirectory(prefix="gateproof-base-") as tmp:
        work = Path(tmp)
        _stage(work)
        result = _run_gate(work, gate)
        if verbose:
            print(result.stdout)
        if result.returncode != 0:
            return False, (result.stdout + result.stderr).strip().splitlines()[-1][:150]
        return True, "green"


def _attack(attack: Attack, verbose: bool) -> tuple[str, str]:
    """Returns (status, detail) where status is BLOCKED / LEAKED / STALE / INVALID."""
    with tempfile.TemporaryDirectory(prefix="gateproof-") as tmp:
        work = Path(tmp)
        _stage(work)

        target = work / attack.path
        original = target.read_text(encoding="utf-8")
        if attack.old not in original:
            return "STALE", (
                f"the text this attack patches is not in {attack.path} — the code moved "
                "and this attack has been silently testing nothing"
            )
        mutated = original.replace(attack.old, attack.new, 1)
        if mutated == original:
            return "STALE", "mutation produced an identical file"
        target.write_text(mutated, encoding="utf-8")

        result = _run_gate(work, attack.gate)
        if verbose:
            print(f"\n--- {attack.name} ---\n{result.stdout}{result.stderr}")

        ok, detail = _blocked_for_the_right_reason(result, attack)
        if ok:
            return "BLOCKED", detail
        return ("LEAKED" if result.returncode == 0 else "INVALID"), detail


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Attack the repo's own gates.")
    parser.add_argument("-v", "--verbose", action="store_true", help="show gate output")
    parser.add_argument("-k", dest="filter", default="", help="only attacks matching this")
    args = parser.parse_args(argv)

    attacks = [a for a in ATTACKS if args.filter in a.name]
    if not attacks:
        print(f"no attack matches {args.filter!r}")
        return 1

    print(f"gate_proof: {len(attacks)} attack(s)\n")

    print("baseline — every gate must be green before an attack proves anything")
    for gate in sorted({a.gate for a in attacks}):
        green, detail = _baseline(gate, args.verbose)
        mark = "ok  " if green else "FAIL"
        print(f"  {mark} {gate}")
        if not green:
            print(f"       {detail}")
            print("\nrefusing to score attacks against an already-red gate.")
            return 1

    print("\nattacks")
    failures = []
    for attack in attacks:
        status, detail = _attack(attack, args.verbose)
        symbol = {"BLOCKED": "ok  ", "LEAKED": "LEAK", "STALE": "STALE", "INVALID": "??? "}[status]
        print(f"  {symbol} {attack.name:28s} {status}")
        print(f"       {detail}")
        if status != "BLOCKED":
            failures.append((attack, status, detail))

    print()
    if failures:
        print(f"{len(failures)}/{len(attacks)} attack(s) were not refused:\n")
        for attack, status, detail in failures:
            print(f"  {attack.name} [{status}]")
            print(f"    why it matters: {attack.rationale}")
            print(f"    gate said:      {detail}\n")
        return 1

    print(f"all {len(attacks)} attacks refused by the right gate for the right reason.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
