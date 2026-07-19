"""Create the Bedrock Knowledge Base's OpenSearch Serverless vector index, from INSIDE the VPC.

Why a Lambda and not the deploy runner
--------------------------------------
Bedrock does not create this index; it requires it to already exist with an exact field
mapping, and there is no Terraform resource for an AOSS data-plane object. The first
implementation ran `scripts/create_kb_index.py` as a `local-exec` on the CI runner. That
cannot work, and the reason is the security posture we deliberately built:

    AllowFromPublic: false
    SourceVPCEs:     [vpce-...]
    SourceServices:  [bedrock.amazonaws.com]

A GitHub-hosted runner is on the public internet — neither inside the VPC nor the Bedrock
service — so AOSS refuses it at the NETWORK layer and reports `401` with an empty body. It
looks exactly like an auth failure, and it survived one round of fixing the data access
policy (which was genuinely also wrong) because the symptom never changed.

The two ways out were to make the collection publicly reachable, or to run the index
creation from inside the VPC. The first would undo a control this repository tests and
attacks on purpose (`AllowFromPublic = false` is asserted, and gate_proof plants `true` to
prove the gate catches it). So: the VPC.

No packaging problem
--------------------
`botocore` and `urllib3` both ship in the AWS Lambda Python runtime, so signing the request
by hand costs nothing and avoids vendoring `opensearch-py` into a deployment zip — the
dependency that made the local-exec brittle in the first place.
"""

from __future__ import annotations

import json
import os

import urllib3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.session import Session

# MUST match `opensearch_serverless_configuration.field_mapping` in knowledge_base.tf.
# A mismatch is not a runtime error — Bedrock accepts the KB and retrieval silently returns
# nothing, which is the worst possible failure for a system that grounds verdicts.
INDEX_BODY = {
    "settings": {"index.knn": True},
    "mappings": {
        "properties": {
            "embedding": {
                "type": "knn_vector",
                "dimension": 1024,  # Titan Text Embeddings v2
                "method": {"name": "hnsw", "engine": "faiss", "space_type": "l2"},
            },
            "text": {"type": "text"},
            "metadata": {"type": "text"},
        }
    },
}


def _signed_put(endpoint: str, index: str, region: str) -> tuple[int, str]:
    url = f"{endpoint.rstrip('/')}/{index}"
    body = json.dumps(INDEX_BODY)

    request = AWSRequest(
        method="PUT", url=url, data=body, headers={"Content-Type": "application/json"}
    )
    # Service name is `aoss`, NOT `es`. Signing for the wrong service is another way to be
    # handed a bare 401.
    credentials = Session().get_credentials()
    SigV4Auth(credentials, "aoss", region).add_auth(request)

    response = urllib3.PoolManager().request(
        "PUT", url, body=body, headers=dict(request.headers), timeout=30.0
    )
    return response.status, response.data.decode("utf-8", "replace")


def lambda_handler(event, context):  # noqa: ANN001, ARG001
    endpoint = os.environ["AOSS_ENDPOINT"]
    index = os.environ["INDEX_NAME"]
    region = os.environ.get("AWS_REGION", "eu-central-1")

    status, payload = _signed_put(endpoint, index, region)

    # Idempotent: re-applying the layer must not fail because the index survived.
    if status in (200, 201):
        return {"created": True, "index": index, "status": status}
    if "resource_already_exists_exception" in payload:
        return {"created": False, "index": index, "status": status, "note": "already exists"}

    raise RuntimeError(f"creating index {index!r} failed: HTTP {status}: {payload[:800]}")
