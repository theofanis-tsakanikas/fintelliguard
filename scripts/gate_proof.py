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
COPY = ("agents", "ml", "pipelines", "simulator", "tests", "pyproject.toml")

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
    # --- the feature contract ------------------------------------------------ #
    Attack(
        name="merchant-risk-table-optional-again",
        rationale=(
            "The exact shape of the dead-feature bug: make the table optional with a "
            "neutral default and every caller silently stops passing it, pinning "
            "merchant_risk_score to a constant while training saw 0.02-0.12."
        ),
        path="ml/features/adapter_stream.py",
        old=(
            'merchant_risk = transforms.risk_score('
            'current["merchant_id"], merchant_risk_table, default=0.0)'
        ),
        new="merchant_risk = 0.0",
        gate="tests/features/test_parity_distributional.py",
        must_fail="test_no_feature_is_a_dead_constant_on_the_serving_path",
    ),
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
