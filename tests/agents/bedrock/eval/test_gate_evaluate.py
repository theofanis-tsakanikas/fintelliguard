"""The verdict-gate evaluate runner scores the labelled set correctly and gates on it.

Mirror of the guardrail red-team coverage test, for the other Tier-2 control: every gold
verdict must be accepted and every adversarial (cheating) verdict rejected, and the runner
must exit non-zero if that ever stops holding.
"""

from __future__ import annotations

from agents.bedrock.eval.evaluate import evaluate_gate, main


def test_gate_accepts_all_gold_and_rejects_all_adversarial():
    report = evaluate_gate()
    assert report.gold > 0 and report.adversarial > 0
    assert report.gold_accepted == report.gold
    assert report.adversarial_rejected == report.adversarial
    assert report.passed


def test_every_labelled_case_decided_correctly():
    report = evaluate_gate()
    assert all(r.correct for r in report.results)


def test_main_exits_zero_on_pass():
    assert main() == 0
