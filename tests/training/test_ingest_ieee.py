"""The IEEE-CIS ingest: idempotent, and loud about which failure it hit.

`docs/DEPLOY.md` §6 carried this as `aws s3 cp ieee-cis/ ... --recursive` — a manual command
naming a 650 MB Kaggle download that was never in the repository. Nothing ran it, so
`bronze.ieee_cis_raw` had no input, `gold.txn_features_training` was never built, and the
serving endpoints failed on every deploy for want of a model that could not be trained.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from ml.training import ingest_ieee


class _FakeS3:
    def __init__(self, existing: set[str] | None = None):
        self.existing = existing or set()
        self.uploaded: list[tuple[str, str]] = []

    def head_object(self, *, Bucket, Key):  # noqa: N803
        if Key in self.existing:
            return {"ContentLength": 1}
        raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")

    def upload_file(self, path, bucket, key):
        self.uploaded.append((bucket, key))


def test_an_existing_object_is_not_downloaded_again():
    """650 MB per deploy is the cost of getting this wrong, and re-deploys are the norm."""
    key = f"{ingest_ieee.PREFIX}{ingest_ieee.FILENAME}"
    s3 = _FakeS3(existing={key})
    assert ingest_ieee.already_present(s3, "bucket", key) is True


def test_a_missing_object_reports_missing_rather_than_raising():
    s3 = _FakeS3()
    assert ingest_ieee.already_present(s3, "bucket", "raw/ieee-cis/nope.csv") is False


def test_a_non_404_s3_error_is_not_swallowed():
    """AccessDenied must not read as 'absent' — that would re-download on every deploy and
    then fail at upload with a different error entirely."""

    class _Denied(_FakeS3):
        def head_object(self, *, Bucket, Key):  # noqa: N803
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "HeadObject")

    with pytest.raises(ClientError):
        ingest_ieee.already_present(_Denied(), "bucket", "key")


def test_missing_kaggle_credentials_fail_before_any_network_call(tmp_path, monkeypatch):
    monkeypatch.delenv("KAGGLE_USERNAME", raising=False)
    monkeypatch.delenv("KAGGLE_KEY", raising=False)
    with pytest.raises(RuntimeError, match="KAGGLE_USERNAME"):
        ingest_ieee.download(tmp_path)


def test_a_download_failure_names_the_rules_acceptance(tmp_path, monkeypatch):
    """403-with-valid-credentials means unaccepted competition rules, and nothing about the
    status code says so. The remedy is a click on a web page, so the error has to say it."""
    monkeypatch.setenv("KAGGLE_USERNAME", "u")
    monkeypatch.setenv("KAGGLE_KEY", "k")

    class _Result:
        returncode = 1
        stdout = ""
        stderr = "403 Forbidden"

    monkeypatch.setattr(ingest_ieee.subprocess, "run", lambda *a, **k: _Result())
    with pytest.raises(RuntimeError, match="rules"):
        ingest_ieee.download(tmp_path)


def test_a_silent_no_op_download_is_an_error(tmp_path, monkeypatch):
    """The CLI can exit 0 having produced nothing. Trusting the exit code alone would upload
    nothing and report success, leaving the pipeline with an empty prefix to read."""
    monkeypatch.setenv("KAGGLE_USERNAME", "u")
    monkeypatch.setenv("KAGGLE_KEY", "k")

    class _Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    monkeypatch.setattr(ingest_ieee.subprocess, "run", lambda *a, **k: _Result())
    with pytest.raises(RuntimeError, match="neither"):
        ingest_ieee.download(tmp_path)


def test_the_prefix_matches_what_the_dlt_pipeline_reads():
    """A mismatch here is invisible: the upload succeeds and Auto Loader finds an empty path,
    so the pipeline produces zero rows and the failure surfaces two steps later in training.
    """
    root = Path(__file__).resolve().parents[2]
    bronze = (root / "pipelines" / "bronze" / "bronze_pipeline.py").read_text("utf-8")
    assert "raw/ieee-cis/" in bronze, "the bronze pipeline no longer reads raw/ieee-cis/"
    assert ingest_ieee.PREFIX == "raw/ieee-cis/"
