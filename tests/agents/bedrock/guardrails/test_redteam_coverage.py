"""Red-team coverage gate for the offline guardrail policy model.

Scope, stated plainly: this scores `policy.py` — a signature model standing in for
Bedrock's ML classifier — against a labelled red-team set. It proves the model's detectors
are wired to the red-team set and that deleting one fails the build (see
`scripts/gate_proof.py::redteam-signature-removed`). It does **not** measure the classifier
that runs in AWS, and the block rate here is a regression score, not a safety metric.

Whether the deployed guardrail is attached, enabled and in sync with this model is a
different question, asked by `test_guardrail_attachment.py`. This file used to try to
answer it by grepping `guardrail.tf` for string literals; those four tests passed for the
entire life of a bug where the guardrail was never bound to the agent, which is why they
are gone rather than merely improved.
"""

from agents.bedrock.guardrails.evaluate import evaluate_coverage


def test_full_block_rate_no_false_positives():
    report = evaluate_coverage()
    assert report.block_rate == 1.0, (
        f"adversarial probes leaked: {report.blocked_adversarial}/{report.adversarial}"
    )
    assert report.false_positive_rate == 0.0, "a benign probe was wrongly blocked"
    assert report.passed


def test_every_category_covered():
    report = evaluate_coverage()
    # all adversarial categories present and fully blocked
    for cat, bucket in report.per_category.items():
        if cat == "benign":
            assert bucket["blocked"] == 0
        else:
            assert bucket["blocked"] == bucket["total"], f"{cat} not fully blocked"
