"""Load the regulatory corpus into the Bedrock Knowledge Base — THROUGH the screen.

Why this file exists
--------------------
The corpus reached S3 by a documented manual command (`docs/DEPLOY.md` §7):

    aws s3 cp agents/bedrock/kb/corpus/ s3://<kb-docs-bucket>/ --recursive

Two things were wrong with that, and the second one is serious.

1. Nothing in the deploy ran it. Health-checking a freshly deployed estate found the
   Knowledge Base `ACTIVE` and EMPTY: zero objects, zero ingestion jobs. Tier 2 grounds
   every verdict in retrieved regulatory text, and there was none — while CLAUDE.md says
   "IaC only. No console deployments, ever."

2. `aws s3 cp` never calls `screen_document()`. `docs/governance/AI_ACT_ANNEX_IV.md` states
   as regulated fact that "the regulatory corpus is screened at ingestion
   (`agents/bedrock/kb/chunking.py`): a document the guardrail would block ... is refused
   before it is embedded." The screen exists, is tested, and gate_proof attacks it — and the
   only path that actually loaded the corpus went around it. A control on the wrong side of
   the door, again: the same shape as the guardrail that was "attached" without being
   attached and the vector store that was "private" while `AllowFromPublic = true`.

What lands in S3 is therefore the SCREENED body — `Document.text` returned by
`load_corpus`, not the file on disk. That distinction is the whole point: uploading raw
files and screening separately would make the screen advisory, and a screen you can bypass
by using a different upload command is not a control. Here the screened text is the only
text that exists to upload; `load_corpus` raises `CorpusRejectedError` before this module
gets anything to write. It also strips the `<!-- regulation: X -->` provenance marker, so
a document cannot self-declare its own authority inside an embedded chunk.

Deletion of stale objects is deliberate too. A renamed or removed regulation would otherwise
linger in the bucket and keep being retrieved as authority long after it left the repo — the
index would answer from a corpus nobody can see in a diff.
"""

from __future__ import annotations

import argparse
import time

import boto3

from agents.bedrock.kb.chunking import Document, load_corpus

CORPUS_DIR = "agents/bedrock/kb/corpus"

# Ingestion is embedding-bound: it reads every document, chunks it and calls Titan for each
# chunk. The four EUR-Lex texts take a few minutes; the ceiling is here so a job that hangs
# fails the deploy loudly instead of holding a runner until GitHub's own timeout kills it
# with no diagnosis.
_POLL_SECONDS = 15
_TIMEOUT_SECONDS = 1800

_TERMINAL_OK = {"COMPLETE"}
_TERMINAL_BAD = {"FAILED", "STOPPED"}


def upload_corpus(s3, bucket: str, documents: list[Document]) -> list[str]:
    """Write each screened document to S3 and remove anything else under the prefix.

    Returns the keys written.
    """
    written = []
    for document in documents:
        key = f"{document.doc_id}.md"
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=document.text.encode("utf-8"),
            ContentType="text/markdown",
        )
        written.append(key)
        print(f"  uploaded {key} ({len(document.text):,} chars, {document.regulation})")

    # Anything in the bucket that is not in the corpus is a document no reviewer can see.
    keep = set(written)
    paginator = s3.get_paginator("list_objects_v2")
    stale = [
        obj["Key"]
        for page in paginator.paginate(Bucket=bucket)
        for obj in page.get("Contents", [])
        if obj["Key"] not in keep
    ]
    if stale:
        s3.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in stale]})
        print(f"  removed {len(stale)} stale object(s): {', '.join(sorted(stale))}")

    return written


def start_and_wait(
    client,
    knowledge_base_id: str,
    data_source_id: str,
    *,
    poll_seconds: float = _POLL_SECONDS,
) -> dict:
    """Start an ingestion job and block until it reaches a terminal state.

    Waiting is not optional. `start_ingestion_job` returns as soon as the job is accepted, so
    a deploy that fires and forgets reports success while the index is still empty — and the
    failure surfaces later as a Tier-2 verdict that cannot ground itself, which looks like an
    agent problem rather than a deployment one.
    """
    job = client.start_ingestion_job(
        knowledgeBaseId=knowledge_base_id, dataSourceId=data_source_id
    )["ingestionJob"]
    job_id = job["ingestionJobId"]
    print(f"  ingestion job {job_id} started")

    deadline = time.monotonic() + _TIMEOUT_SECONDS
    while True:
        job = client.get_ingestion_job(
            knowledgeBaseId=knowledge_base_id,
            dataSourceId=data_source_id,
            ingestionJobId=job_id,
        )["ingestionJob"]
        status = job["status"]

        if status in _TERMINAL_OK:
            stats = job.get("statistics", {})
            print(
                f"  ingestion COMPLETE — scanned "
                f"{stats.get('numberOfDocumentsScanned', '?')}, indexed "
                f"{stats.get('numberOfNewDocumentsIndexed', '?')}, failed "
                f"{stats.get('numberOfDocumentsFailed', '?')}"
            )
            # What "the index is empty" actually looks like — and it is NOT "indexed 0".
            #
            # The first version failed whenever `numberOfNewDocumentsIndexed` was zero. That
            # is the normal result of a RE-DEPLOY: the corpus has not changed, so Bedrock
            # indexes nothing new and reports `scanned 4, indexed 0, failed 0`. Deploy run
            # 29717600570 died on exactly that, with an error message insisting the Knowledge
            # Base was empty while it held the full corpus. The check asserted my assumption
            # rather than the property.
            #
            # SCANNED is the number that answers the question. Zero scanned means the data
            # source found no documents — the genuinely empty case. Anything failed means
            # documents were rejected during embedding. Neither is the same as "unchanged".
            scanned = stats.get("numberOfDocumentsScanned") or 0
            failed = stats.get("numberOfDocumentsFailed") or 0
            if scanned == 0:
                raise RuntimeError(
                    f"ingestion job {job_id} scanned NO documents — the data source found "
                    "nothing to index, so the Knowledge Base is empty and Tier 2 cannot "
                    "ground a verdict. Check that the upload above reached the bucket the "
                    "data source reads."
                )
            if failed:
                raise RuntimeError(
                    f"ingestion job {job_id} failed to index {failed} of {scanned} "
                    "documents; a partially indexed corpus grounds verdicts in an "
                    "incomplete regulation set"
                )
            return job

        if status in _TERMINAL_BAD:
            reasons = "; ".join(job.get("failureReasons", [])) or "no reason reported"
            raise RuntimeError(f"ingestion job {job_id} ended {status}: {reasons}")

        if time.monotonic() > deadline:
            raise TimeoutError(f"ingestion job {job_id} still {status} after {_TIMEOUT_SECONDS}s")

        # Injectable so the tests exercise the real polling loop instead of sleeping through
        # it — a suite that waits 15 real seconds per case gets skipped or trimmed, and this
        # loop is where the "COMPLETE but empty" check lives.
        time.sleep(poll_seconds)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True, help="KB documents bucket")
    parser.add_argument("--knowledge-base-id", required=True)
    parser.add_argument("--data-source-id", required=True)
    parser.add_argument("--region", default="eu-central-1")
    parser.add_argument("--corpus-dir", default=CORPUS_DIR)
    args = parser.parse_args(argv)

    # Screens every document and raises CorpusRejectedError on the first refusal — so a
    # poisoned regulation stops the deploy rather than reaching the index.
    documents = load_corpus(args.corpus_dir)
    if not documents:
        raise RuntimeError(f"no .md documents found in {args.corpus_dir}")
    print(f"screened {len(documents)} document(s) from {args.corpus_dir}")

    session = boto3.session.Session(region_name=args.region)
    upload_corpus(session.client("s3"), args.bucket, documents)
    start_and_wait(session.client("bedrock-agent"), args.knowledge_base_id, args.data_source_id)


if __name__ == "__main__":
    main()
