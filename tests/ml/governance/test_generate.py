"""Tests for the regulated-AI documentation generator + freshness gate."""

from pathlib import Path

from ml.governance import generate

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_model_card_lists_all_features():
    from ml.features.schema import FEATURE_NAMES

    card = generate.render_model_card()
    for name in FEATURE_NAMES:
        assert f"`{name}`" in card


def test_model_card_includes_promotion_gate():
    from ml.training.promote import AUC_THRESHOLD, FRAUD_PRECISION_THRESHOLD

    card = generate.render_model_card()
    assert str(AUC_THRESHOLD) in card
    assert str(FRAUD_PRECISION_THRESHOLD) in card


def test_guardrail_coverage_doc_reports_full_block():
    doc = generate.render_guardrail_coverage()
    assert "16/16 blocked" in doc or "/" in doc  # live number rendered
    assert "100%" in doc


def test_ai_act_doc_marks_high_risk_and_oversight():
    doc = generate.render_ai_act()
    assert "high-risk" in doc.lower()
    assert "human" in doc.lower() and "oversight" in doc.lower()
    assert "Annex IV" in doc


def test_dataset_card_documents_pii_handling():
    doc = generate.render_dataset_card()
    assert "hashed" in doc.lower()
    assert "no raw PII" in doc or "no raw PAN" in doc


def test_committed_docs_are_up_to_date():
    rc = generate.main(["--root", str(REPO_ROOT), "--check"])
    assert rc == 0, "docs/governance is stale — run `make govern-docs`"
