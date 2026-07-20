"""The corpus must reach the Knowledge Base only through the screen.

A post-deploy health check found the KB `ACTIVE` and EMPTY — zero objects, zero ingestion
jobs — because nothing in the pipeline loaded it. `docs/DEPLOY.md` carried the load as a
manual `aws s3 cp ... --recursive`, which no CI step ran and which never calls
`screen_document()`.

That second half is the one these tests exist for. `docs/governance/AI_ACT_ANNEX_IV.md`
states as regulated fact that the corpus is screened at ingestion. The screen was written,
tested, and attacked by gate_proof — and the only path that actually loaded documents went
around it. These pin the path, not just the function.
"""

from __future__ import annotations

import pytest

from agents.bedrock.kb.chunking import Document
from agents.bedrock.kb.ingest import start_and_wait, upload_corpus
from tests.cicd.test_workflows import _load


class _FakeS3:
    def __init__(self, existing: list[str] | None = None):
        self.objects = dict.fromkeys(existing or [], b"stale")
        self.deleted: list[str] = []

    def put_object(self, *, Bucket, Key, Body, ContentType):  # noqa: N803
        self.objects[Key] = Body

    def get_paginator(self, _name):
        outer = self

        class _P:
            def paginate(self, **_kwargs):
                return [{"Contents": [{"Key": k} for k in outer.objects]}]

        return _P()

    def delete_objects(self, *, Bucket, Delete):  # noqa: N803
        for entry in Delete["Objects"]:
            self.deleted.append(entry["Key"])
            self.objects.pop(entry["Key"], None)


class _FakeAgent:
    """Returns a scripted sequence of ingestion-job states."""

    def __init__(self, states: list[dict]):
        self.states = list(states)

    def start_ingestion_job(self, **_kwargs):
        return {"ingestionJob": {"ingestionJobId": "job-1", "status": "STARTING"}}

    def get_ingestion_job(self, **_kwargs):
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return {"ingestionJob": {"ingestionJobId": "job-1", **state}}


def _doc(text: str = "Article 97 requires strong customer authentication.") -> Document:
    return Document(doc_id="psd2", source="psd2.md", regulation="PSD2", text=text)


# --------------------------------------------------------------------------- #
# The path
# --------------------------------------------------------------------------- #


def test_the_deploy_loads_the_corpus_through_the_screening_module():
    """The load-bearing one: a raw `aws s3 cp` would put the corpus in the index unscreened.

    `screen_document` refusing a poisoned regulation means nothing if the deploy uploads the
    files by another route — the control would sit on the wrong side of the door, exactly as
    the guardrail did when it was "attached" without being attached.
    """
    steps = _load("deploy")["jobs"]["apply"]["steps"]
    scripts = "\n".join(s["run"] for s in steps if "run" in s)

    assert "agents.bedrock.kb.ingest" in scripts, (
        "the deploy does not run the screening ingester, so the Knowledge Base is either "
        "empty or loaded by a path that never calls screen_document()"
    )
    assert "aws s3 cp" not in scripts and "aws s3 sync" not in scripts, (
        "the deploy copies the corpus to S3 directly — that bypasses screen_document(), "
        "which docs/governance/AI_ACT_ANNEX_IV.md states as regulated fact is applied"
    )


def test_the_ids_the_ingester_needs_are_terraform_outputs():
    """Pasting a knowledge-base or data-source id by hand is a console visit with extra steps."""
    outputs = __import__("pathlib").Path("agents/bedrock/terraform/outputs.tf").read_text("utf-8")
    for name in ("kb_docs_bucket", "knowledge_base_id", "data_source_id"):
        assert f'output "{name}"' in outputs, (
            f"{name} is not an output, so the deploy cannot resolve it and someone has to "
            "read it off the console"
        )


# --------------------------------------------------------------------------- #
# What actually lands in the bucket
# --------------------------------------------------------------------------- #


def test_what_is_uploaded_is_the_screened_text_not_the_file_on_disk():
    """`load_corpus` strips the provenance marker; the bucket must get THAT, not the source.

    Uploading raw files and screening separately makes the screen advisory. Here the screened
    body is the only text this function is given.
    """
    s3 = _FakeS3()
    document = _doc("Article 97 requires SCA.")
    upload_corpus(s3, "bucket", [document])
    assert s3.objects["psd2.md"] == b"Article 97 requires SCA."


def test_a_document_removed_from_the_corpus_is_removed_from_the_bucket():
    """A regulation deleted in a commit must stop being retrievable as authority.

    Otherwise the index keeps answering from a corpus nobody can see in a diff.
    """
    s3 = _FakeS3(existing=["repealed_directive.md"])
    upload_corpus(s3, "bucket", [_doc()])
    assert "repealed_directive.md" in s3.deleted
    assert set(s3.objects) == {"psd2.md"}


# --------------------------------------------------------------------------- #
# Waiting on the job
# --------------------------------------------------------------------------- #


def test_a_completed_job_that_indexed_nothing_is_a_failure():
    """`COMPLETE` with zero documents indexed leaves exactly the empty KB this prevents.

    Reported as success it would surface much later as a Tier-2 verdict that cannot ground
    itself — which reads as an agent problem rather than a deployment one.
    """
    agent = _FakeAgent([{"status": "COMPLETE", "statistics": {"numberOfNewDocumentsIndexed": 0}}])
    with pytest.raises(RuntimeError, match="without indexing any document"):
        start_and_wait(agent, "kb-1", "ds-1", poll_seconds=0)


def test_a_failed_job_raises_with_the_reason_bedrock_gave():
    agent = _FakeAgent([{"status": "FAILED", "failureReasons": ["embedding model access denied"]}])
    with pytest.raises(RuntimeError, match="embedding model access denied"):
        start_and_wait(agent, "kb-1", "ds-1", poll_seconds=0)


def test_a_successful_job_returns_once_documents_are_indexed():
    agent = _FakeAgent(
        [
            {"status": "IN_PROGRESS"},
            {"status": "COMPLETE", "statistics": {"numberOfNewDocumentsIndexed": 4}},
        ]
    )
    job = start_and_wait(agent, "kb-1", "ds-1", poll_seconds=0)
    assert job["status"] == "COMPLETE"
