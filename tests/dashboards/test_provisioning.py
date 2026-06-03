"""Provisioning YAML: valid, no secrets, datasource uids match the dashboards."""

from __future__ import annotations

import yaml

from ._helpers import (
    DATABRICKS_DS_UID,
    PROMETHEUS_DS_UID,
    PROVISIONING_DIR,
    iter_targets,
    load_dashboards,
)

_DATASOURCES_YAML = PROVISIONING_DIR / "datasources.yaml"
_DASHBOARDS_YAML = PROVISIONING_DIR / "dashboards.yaml"


def _datasources():
    return yaml.safe_load(_DATASOURCES_YAML.read_text(encoding="utf-8"))["datasources"]


def test_datasources_declare_expected_uids():
    uids = {ds["uid"] for ds in _datasources()}
    assert {DATABRICKS_DS_UID, PROMETHEUS_DS_UID} <= uids
    for ds in _datasources():
        assert ds.get("name") and ds.get("type")


def test_no_secrets_only_env_references():
    text = _DATASOURCES_YAML.read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        if key.strip().lower() in {"token", "password", "secret", "apikey", "api_key"}:
            assert "${" in value, f"secret-like field not an env ref: {line}"
    # No literal Databricks token prefix anywhere.
    assert "dapi" not in text


def test_dashboards_only_reference_declared_datasources():
    declared = {ds["uid"] for ds in _datasources()}
    for dashboard in load_dashboards().values():
        for panel, _target in iter_targets(dashboard):
            assert panel["datasource"]["uid"] in declared


def test_dashboard_provider_yaml_valid():
    providers = yaml.safe_load(_DASHBOARDS_YAML.read_text(encoding="utf-8"))["providers"]
    assert providers and providers[0]["type"] == "file"
