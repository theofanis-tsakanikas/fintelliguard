"""Run the verdict-acceptance gate over the labelled verdict set and report.

The correctness counterpart to `agents.bedrock.guardrails.evaluate`. The guardrail proves
the output is SAFE (no PII leak, no jailbreak); this gate proves the verdict is CORRECT —
no fabricated citation, no invented driver, no softened decision. It scores the labelled
verdict set: every gold verdict must be accepted, every adversarial (cheating) one must be
rejected. Used as a CI gate and as a source for the governance docs.

    python -m agents.bedrock.eval.evaluate
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from agents.bedrock.eval.judge import evaluate_verdict
from agents.bedrock.eval.verdicts import VerdictCase, verdict_cases


@dataclass(frozen=True)
class GateCaseResult:
    case: VerdictCase
    accepted: bool
    failures: tuple[str, ...]
    correct: bool  # accepted == should_accept


@dataclass(frozen=True)
class GateReport:
    """Aggregate verdict-gate outcomes over the labelled set."""

    total: int
    gold: int
    adversarial: int
    gold_accepted: int
    adversarial_rejected: int
    results: tuple[GateCaseResult, ...]

    @property
    def passed(self) -> bool:
        """Gate: every gold verdict accepted, every adversarial verdict rejected."""
        return self.gold_accepted == self.gold and self.adversarial_rejected == self.adversarial


def evaluate_case(case: VerdictCase) -> GateCaseResult:
    """Run one candidate verdict through the gate and check the outcome is as labelled."""
    result = evaluate_verdict(case.verdict, case.context)
    return GateCaseResult(
        case=case,
        accepted=result.accepted,
        failures=result.failures,
        correct=result.accepted == case.should_accept,
    )


def evaluate_gate(cases: Sequence[VerdictCase] | None = None) -> GateReport:
    """Run the whole labelled verdict set and aggregate."""
    cases = tuple(cases) if cases is not None else verdict_cases()
    results = tuple(evaluate_case(c) for c in cases)
    gold = [r for r in results if r.case.should_accept]
    adversarial = [r for r in results if not r.case.should_accept]
    return GateReport(
        total=len(results),
        gold=len(gold),
        adversarial=len(adversarial),
        gold_accepted=sum(int(r.accepted) for r in gold),
        adversarial_rejected=sum(int(not r.accepted) for r in adversarial),
        results=results,
    )


def main() -> int:
    report = evaluate_gate()
    print(
        f"verdict gate: gold accepted {report.gold_accepted}/{report.gold}  "
        f"adversarial rejected {report.adversarial_rejected}/{report.adversarial}"
    )
    print("  checks: schema · no_pii · grounding · faithfulness · decision")
    # Which check caught each class of cheating verdict.
    groups: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # {check: [rejected, total]}
    for r in report.results:
        if not r.case.should_accept:
            key = r.case.expected_failure or "other"
            groups[key][1] += 1
            groups[key][0] += int(not r.accepted)
    for check, (rejected, total) in sorted(groups.items()):
        print(f"  {check:14} rejected {rejected}/{total}")
    print("RESULT:", "PASS" if report.passed else "FAIL")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
