"""Isolate training tests' working directory.

`mlflow.*.log_model` drops a local `mlruns/` in the CWD (independent of the tracking
URI). Running each test from its own tmp dir keeps that scratch out of the repo.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
