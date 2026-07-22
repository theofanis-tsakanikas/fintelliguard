"""Log + register the served copilot pyfunc to Unity Catalog.

Runs on the deploy RUNNER (not a cluster): it bundles a feature snapshot (the repo's
`agents/databricks/demo_transactions.json`, parity-tested against the Tier-2 online store) into
the model and logs to the Databricks UC registry over the workspace OAuth the runner already
holds. No cluster spins up — the copilot is a pyfunc, not a training run.

The `resources` list is what makes serving-time auth work: Databricks Model Serving mints a
scoped credential for exactly the fraud-score endpoint, the LLM endpoint and the vector index
the copilot calls, so the served model reaches them without embedded secrets.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import mlflow
import pandas as pd
from mlflow.models import ModelSignature
from mlflow.models.resources import DatabricksServingEndpoint, DatabricksVectorSearchIndex
from mlflow.tracking import MlflowClient
from mlflow.types import ColSpec, Schema

from agents.databricks.copilot_pyfunc import CONFIG_ARTIFACT, FEATURES_ARTIFACT, CopilotPyfunc

_INPUT = Schema(
    [
        ColSpec("string", "query"),
        ColSpec("string", "transaction_id"),
        ColSpec("string", "card_hash"),
    ]
)
_OUTPUT = Schema([ColSpec("string")])  # one JSON investigation brief per row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-name", required=True)
    ap.add_argument("--fraud-endpoint", required=True)
    ap.add_argument("--llm-endpoint", required=True)
    ap.add_argument("--vector-endpoint", required=True)
    ap.add_argument("--vector-index", required=True)
    ap.add_argument("--features-file", required=True, help="local path to the online-features JSON")
    ap.add_argument("--alias", default="production")
    args = ap.parse_args()

    mlflow.set_tracking_uri("databricks")
    mlflow.set_registry_uri("databricks-uc")
    mlflow.set_experiment("/Shared/fintelliguard-copilot")

    features = json.loads(Path(args.features_file).read_text(encoding="utf-8"))
    config = {
        "fraud_endpoint": args.fraud_endpoint,
        "llm_endpoint": args.llm_endpoint,
        "vector_endpoint": args.vector_endpoint,
        "vector_index": args.vector_index,
    }

    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "config.json"
        feat_path = Path(tmp) / "online_features.json"
        cfg_path.write_text(json.dumps(config), encoding="utf-8")
        feat_path.write_text(json.dumps(features), encoding="utf-8")

        example = pd.DataFrame(
            [
                {
                    "query": "why was this flagged?",
                    "transaction_id": "txn_demo_fraud_001",
                    "card_hash": "card_demo_hi_risk",
                }
            ]
        )

        with mlflow.start_run(run_name="register-copilot"):
            info = mlflow.pyfunc.log_model(
                artifact_path="copilot",
                python_model=CopilotPyfunc(),
                artifacts={CONFIG_ARTIFACT: str(cfg_path), FEATURES_ARTIFACT: str(feat_path)},
                # The copilot imports agents.databricks.* and its tools; ml is on the path for
                # the shared feature schema. Relative to the runner CWD (the repo root).
                code_paths=["agents", "ml"],
                pip_requirements=[
                    "mlflow",
                    "pandas",
                    "databricks-vectorsearch",
                    "databricks-sdk",
                ],
                signature=ModelSignature(inputs=_INPUT, outputs=_OUTPUT),
                input_example=example,
                resources=[
                    DatabricksServingEndpoint(endpoint_name=args.fraud_endpoint),
                    DatabricksServingEndpoint(endpoint_name=args.llm_endpoint),
                    DatabricksVectorSearchIndex(index_name=args.vector_index),
                ],
                registered_model_name=args.model_name,
            )

    client = MlflowClient(registry_uri="databricks-uc")
    client.set_registered_model_alias(args.model_name, args.alias, info.registered_model_version)
    print(f"registered {args.model_name} v{info.registered_model_version} -> alias '{args.alias}'")


if __name__ == "__main__":
    main()
