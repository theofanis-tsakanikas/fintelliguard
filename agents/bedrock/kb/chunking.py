"""Regulatory corpus chunking with chunk-level metadata.

Splits documents into overlapping, word-boundary-aligned chunks carrying source +
regulation + position metadata — the input a KB ingestion job vectorizes. Pure functions,
no cloud.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

# Optional inline marker in a doc: <!-- regulation: AML -->
_REGULATION_MARKER = re.compile(r"<!--\s*regulation:\s*(?P<reg>[^>]+?)\s*-->", re.IGNORECASE)


@dataclass(frozen=True)
class Document:
    """A source regulatory document before chunking."""

    doc_id: str
    source: str
    regulation: str
    text: str


@dataclass(frozen=True)
class Chunk:
    """One chunk plus the metadata a vector index stores alongside the embedding."""

    chunk_id: str
    text: str
    source: str
    regulation: str
    chunk_index: int
    start_char: int
    end_char: int

    def to_metadata(self) -> dict:
        """Chunk-level metadata (everything except the raw text)."""
        meta = asdict(self)
        meta.pop("text")
        return meta


def chunk_text(
    text: str,
    *,
    doc_id: str,
    source: str,
    regulation: str,
    max_chars: int = 800,
    overlap: int = 120,
) -> list[Chunk]:
    """Split `text` into overlapping chunks aligned to word boundaries where possible."""
    if overlap < 0 or max_chars <= 0:
        raise ValueError("max_chars must be > 0 and overlap >= 0")
    if overlap >= max_chars:
        raise ValueError("overlap must be smaller than max_chars")

    text = text.strip()
    if not text:
        return []

    chunks: list[Chunk] = []
    n = len(text)
    start = 0
    index = 0
    while start < n:
        end = min(start + max_chars, n)
        if end < n:
            # Prefer a word boundary, but never before this floor (guarantees progress).
            floor = start + max_chars - overlap
            cut = text.rfind(" ", floor, end)
            if cut > start:
                end = cut

        piece = text[start:end].strip()
        if piece:
            chunks.append(
                Chunk(
                    chunk_id=f"{doc_id}-{index:04d}",
                    text=piece,
                    source=source,
                    regulation=regulation,
                    chunk_index=index,
                    start_char=start,
                    end_char=end,
                )
            )
            index += 1

        if end >= n:
            break
        next_start = end - overlap
        start = next_start if next_start > start else end  # always make progress
    return chunks


def chunk_documents(documents: list[Document], **kwargs) -> list[Chunk]:
    """Chunk every document, flattening into one list (chunk ids stay doc-scoped)."""
    chunks: list[Chunk] = []
    for document in documents:
        chunks.extend(
            chunk_text(
                document.text,
                doc_id=document.doc_id,
                source=document.source,
                regulation=document.regulation,
                **kwargs,
            )
        )
    return chunks


def load_corpus(directory: str | Path) -> list[Document]:
    """Load `.md` regulatory docs from a directory.

    `regulation` comes from an inline `<!-- regulation: X -->` marker if present, else
    "general". `doc_id` is the file stem; `source` is the file name.
    """
    base = Path(directory)
    documents: list[Document] = []
    for path in sorted(base.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        match = _REGULATION_MARKER.search(text)
        regulation = match.group("reg").strip() if match else "general"
        documents.append(
            Document(doc_id=path.stem, source=path.name, regulation=regulation, text=text)
        )
    return documents
