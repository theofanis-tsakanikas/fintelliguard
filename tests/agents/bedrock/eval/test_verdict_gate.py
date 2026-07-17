"""Tests for the verdict acceptance gate over the labelled verdict set."""

from agents.bedrock.eval.judge import VerdictContext, evaluate_verdict
from agents.bedrock.eval.verdicts import verdict_cases

CTX = VerdictContext(
    top_features=("txn_velocity_1h", "country_mismatch", "amount_zscore"),
    retrieved_references=("PSD2 Art. 97 (SCA)", "AMLD5 Art. 18 (enhanced due diligence)"),
    decision_hint="review",
)


def test_labeled_set_matches_expected_outcomes():
    for case in verdict_cases():
        result = evaluate_verdict(case.verdict, case.context)
        assert result.accepted == case.should_accept, f"{case.name}: {result.failures}"
        if not case.should_accept:
            joined = " ".join(result.failures)
            assert case.expected_failure in joined, (
                f"{case.name}: expected '{case.expected_failure}' in {joined}"
            )


def test_gold_verdict_passes_all_checks():
    verdict = {
        "fraud_score": 0.78,
        "reasoning": "A velocity spike with a country mismatch supports enhanced due diligence.",
        "drivers": ["txn_velocity_1h", "country_mismatch"],
        "regulatory_reference": "AMLD5 Art. 18 (enhanced due diligence)",
        "recommended_action": "review",
    }
    result = evaluate_verdict(verdict, CTX)
    assert result.accepted
    assert all(result.checks.values())


def test_hallucinated_reference_fails_grounding():
    verdict = {
        "fraud_score": 0.78,
        "reasoning": "A velocity spike.",
        "regulatory_reference": "PSD2 Art. 999 (invented)",
        "recommended_action": "review",
    }
    result = evaluate_verdict(verdict, CTX)
    assert not result.accepted
    assert result.checks["grounding"] is False


def test_invented_driver_fails_faithfulness():
    verdict = {
        "fraud_score": 0.78,
        "reasoning": "A new device at an unusual hour drove this.",
        "regulatory_reference": "PSD2 Art. 97 (SCA)",
        "recommended_action": "review",
    }
    result = evaluate_verdict(verdict, CTX)
    assert result.checks["faithfulness"] is False


def test_raw_pii_fails():
    verdict = {
        "fraud_score": 0.78,
        "reasoning": "Card 4111 1111 1111 1111 shows a velocity spike.",
        "regulatory_reference": "PSD2 Art. 97 (SCA)",
        "recommended_action": "review",
    }
    assert evaluate_verdict(verdict, CTX).checks["no_pii"] is False


def test_invalid_action_fails_decision():
    verdict = {
        "fraud_score": 0.78,
        "reasoning": "A velocity spike.",
        "regulatory_reference": "PSD2 Art. 97 (SCA)",
        "recommended_action": "approve",  # not allow/review/block
    }
    assert evaluate_verdict(verdict, CTX).checks["decision"] is False


def test_justified_escalation_accepted():
    verdict = {
        "fraud_score": 0.85,
        "reasoning": "Country mismatch and amount anomaly justify escalating beyond the hint.",
        "drivers": ["country_mismatch", "amount_zscore"],
        "regulatory_reference": "PSD2 Art. 97 (SCA)",
        "recommended_action": "block",
    }
    assert evaluate_verdict(verdict, CTX).accepted
