"""The critical single-node job clusters must be ON_DEMAND, not spot.

A DABs `new_cluster` with no `aws_attributes.availability` defaults to spot-with-fallback, and a
SPOT_INSTANCE_TERMINATION on a single-node job is NOT auto-retried: it fails the task. On the
seed job it failed a whole deploy after 14 minutes (run 29891620343); on the training job a
mid-run kill loses the run and the gate re-trains — paying the cluster twice, the exact
wasted-retrain cost this project works to remove. DLT is excluded on purpose: it auto-retries a
lost node, so spot's saving is safe there.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]

# (bundle file, dotted path to the job) -> the job whose single-node cluster must be on-demand.
_CRITICAL = [
    ("infra/bundles/prereq/databricks.yml", ["seed_resolved_cases"]),
    ("infra/bundles/resources/training.yml", ["train_fraud_scorer"]),
]


def _find_new_clusters(node) -> list[dict]:
    found = []
    if isinstance(node, dict):
        if "new_cluster" in node and isinstance(node["new_cluster"], dict):
            found.append(node["new_cluster"])
        for value in node.values():
            found.extend(_find_new_clusters(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_find_new_clusters(value))
    return found


def test_critical_job_clusters_are_on_demand_not_spot():
    for rel, job_path in _CRITICAL:
        doc = yaml.safe_load((_ROOT / rel).read_text(encoding="utf-8"))
        job = doc["resources"]["jobs"][job_path[0]]
        clusters = _find_new_clusters(job)
        assert clusters, f"{rel}:{job_path[0]} defines no new_cluster to check"
        for cluster in clusters:
            availability = cluster.get("aws_attributes", {}).get("availability")
            assert availability == "ON_DEMAND", (
                f"{rel}:{job_path[0]} cluster availability is {availability!r}, not ON_DEMAND — "
                "a spot kill on this non-retried single-node job fails the task and wastes the "
                "cluster time (SPOT_INSTANCE_TERMINATION, run 29891620343)"
            )
