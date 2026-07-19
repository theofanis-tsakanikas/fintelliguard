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
`botocore` ships in the AWS Lambda Python runtime and carries both the SigV4 signer and an
HTTP session, so signing and sending cost nothing and avoid vendoring `opensearch-py` into
a deployment zip — the dependency that made the local-exec brittle in the first place.
"""

from __future__ import annotations

import hashlib
import json
import os
import time

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.httpsession import URLLib3Session
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
    """PUT the index mapping, SigV4-signed for `aoss`.

    Sent with botocore's own HTTP session, deliberately. A SigV4 signature covers the
    method, the canonical URI, and a specific set of HEADERS including Host — so signing an
    `AWSRequest` and then re-assembling the call by hand for another HTTP client means the
    bytes that go out are not quite the bytes that were signed, and the service rejects it
    with a flat `403 Forbidden` that says nothing about signatures. That is what the first
    version of this function did, and it cost two deploys: the retry loop below dutifully
    retried a request that could never succeed.

    `URLLib3Session().send(request.prepare())` transmits exactly what was signed.
    """
    url = f"{endpoint.rstrip('/')}/{index}"
    body = json.dumps(INDEX_BODY)

    # `X-Amz-Content-SHA256` is REQUIRED by AOSS on any signed request that carries a body,
    # and botocore's `SigV4Auth` does not add it — it hashes the payload into the canonical
    # request but sends no header. (`opensearch-py`'s AWSV4SignerAuth special-cases `aoss`
    # and adds it, which is why the original script would have worked had it been able to
    # reach the collection at all.)
    #
    # Omitting it does not produce a signature error. It produces a bare `403 Forbidden`,
    # identical to an authorization failure — which is why this cost several deploys and was
    # only settled by probing the live endpoint: a PUT with NO body returned 200 while the
    # same PUT with `{}` returned 403. Not the mapping, not the permissions: the header.
    headers = {
        "Content-Type": "application/json",
        "X-Amz-Content-SHA256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
    }
    request = AWSRequest(method="PUT", url=url, data=body, headers=headers)
    # Service name is `aoss`, NOT `es`. Signing for the wrong service is another way to be
    # handed a bare 401.
    credentials = Session().get_credentials()
    SigV4Auth(credentials, "aoss", region).add_auth(request)

    response = URLLib3Session(timeout=30).send(request.prepare())
    payload = response.content or b""
    return response.status_code, payload.decode("utf-8", "replace")


def lambda_handler(event, context):  # noqa: ANN001, ARG001
    endpoint = os.environ["AOSS_ENDPOINT"]
    index = os.environ["INDEX_NAME"]
    region = os.environ.get("AWS_REGION", "eu-central-1")

    # AOSS data access policies are eventually consistent, and this Lambda is invoked in the
    # same apply that grants its role. The first attempt therefore raced the grant and came
    # back `403 Forbidden` against a policy that — checked live, moments later — already
    # named the role and already carried `aoss:CreateIndex`.
    #
    # Retrying rather than sleeping a fixed amount: the delay is not a known quantity, and a
    # constant long enough to be safe is a constant wasted on every run that did not need it.
    # 403 is the only retryable status here — 401 means the network path is wrong (see the
    # module docstring) and no amount of waiting fixes it.
    status, payload = 0, ""
    for attempt in range(6):
        if attempt:
            time.sleep(min(2**attempt, 20))
        status, payload = _signed_put(endpoint, index, region)

        if status in (200, 201):
            return {"created": True, "index": index, "status": status, "attempts": attempt + 1}
        # Idempotent: re-applying the layer must not fail because the index survived.
        if "resource_already_exists_exception" in payload:
            return {"created": False, "index": index, "note": "already exists"}
        if status != 403:
            break

    raise RuntimeError(f"creating index {index!r} failed: HTTP {status}: {payload[:800]}")
