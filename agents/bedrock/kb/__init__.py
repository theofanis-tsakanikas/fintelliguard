"""Knowledge Base document preparation (chunking) for the regulatory corpus.

Pure, tested chunking. Actual KB ingestion (S3 + OpenSearch Serverless vectorization)
runs on Bedrock and is deferred to the deploy phase.
"""

from __future__ import annotations

from agents.bedrock.kb.chunking import (
    Chunk,
    Document,
    chunk_documents,
    chunk_text,
    load_corpus,
)

__all__ = ["Chunk", "Document", "chunk_documents", "chunk_text", "load_corpus"]
