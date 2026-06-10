"""KB chunking: sizing, overlap, metadata, corpus loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.bedrock.kb.chunking import (
    Document,
    chunk_documents,
    chunk_text,
    load_corpus,
)

_TEXT = " ".join(f"word{i}" for i in range(400))  # ~2700 chars, plenty to split


def test_chunks_respect_max_size_and_carry_metadata():
    chunks = chunk_text(
        _TEXT, doc_id="d1", source="d1.md", regulation="AML", max_chars=300, overlap=50
    )
    assert len(chunks) > 1
    for i, chunk in enumerate(chunks):
        assert chunk.text
        assert len(chunk.text) <= 300
        assert chunk.chunk_index == i
        assert chunk.chunk_id == f"d1-{i:04d}"
        assert chunk.source == "d1.md"
        assert chunk.regulation == "AML"
        assert "text" not in chunk.to_metadata()
        assert chunk.to_metadata()["chunk_id"] == chunk.chunk_id


def test_consecutive_chunks_overlap():
    chunks = chunk_text(_TEXT, doc_id="d", source="s", regulation="r", max_chars=300, overlap=50)
    for earlier, later in zip(chunks, chunks[1:], strict=False):
        assert later.start_char < earlier.end_char  # overlapping windows


def test_short_text_is_single_chunk_and_empty_is_none():
    one = chunk_text("a short clause", doc_id="d", source="s", regulation="r")
    assert len(one) == 1 and one[0].chunk_index == 0
    assert chunk_text("   ", doc_id="d", source="s", regulation="r") == []


def test_invalid_overlap_raises():
    with pytest.raises(ValueError, match="overlap must be smaller"):
        chunk_text("x", doc_id="d", source="s", regulation="r", max_chars=100, overlap=100)


def test_chunk_documents_keeps_doc_scoped_ids():
    docs = [
        Document(doc_id="aml", source="aml.md", regulation="AML", text=_TEXT),
        Document(doc_id="psd2", source="psd2.md", regulation="PSD2", text=_TEXT),
    ]
    chunks = chunk_documents(docs, max_chars=300, overlap=50)
    assert any(c.chunk_id.startswith("aml-") for c in chunks)
    assert any(c.chunk_id.startswith("psd2-") for c in chunks)
    assert {c.regulation for c in chunks} == {"AML", "PSD2"}


def test_load_corpus_reads_regulation_marker():
    corpus_dir = Path(__file__).resolve().parents[3] / "agents" / "bedrock" / "kb" / "corpus"
    docs = load_corpus(corpus_dir)
    assert docs, "expected at least the sample regulatory doc"
    sample = next(d for d in docs if d.doc_id == "aml_psd2_excerpt")
    assert sample.regulation == "AML/PSD2"  # parsed from the inline marker
    assert chunk_documents(docs)  # the loaded corpus chunks cleanly
