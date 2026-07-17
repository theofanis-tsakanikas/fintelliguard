#!/usr/bin/env python3
"""Create the OpenSearch Serverless vector index the Bedrock Knowledge Base requires.

Bedrock expects the index to pre-exist with an exact field mapping; it does not create it,
and there is no AWS-provider resource for an AOSS vector index — it is a data-plane object
created over the collection's HTTPS endpoint with SigV4. So `knowledge_base.tf` wires this
script as a `local-exec` provisioner that runs after the collection and its access policy
exist and before the KB references the index.

Idempotent: a 400 "resource_already_exists_exception" is success, so a re-apply does not
fail. Requires `opensearch-py` with the AWS auth extra; the deploy runner installs it.

Env: AOSS_ENDPOINT, INDEX_NAME, AWS_REGION.
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    endpoint = os.environ["AOSS_ENDPOINT"].replace("https://", "")
    index = os.environ["INDEX_NAME"]
    region = os.environ["AWS_REGION"]

    try:
        import boto3
        from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection
    except ImportError:
        sys.stderr.write(
            "create_kb_index needs boto3 + opensearch-py[async]; the deploy runner installs "
            "them. This is the KB index step from docs/DEPLOY.md.\n"
        )
        return 1

    auth = AWSV4SignerAuth(boto3.Session().get_credentials(), region, "aoss")
    client = OpenSearch(
        hosts=[{"host": endpoint, "port": 443}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
    )

    # The mapping MUST match `opensearch_serverless_configuration.field_mapping` in
    # knowledge_base.tf: embedding (knn_vector), text, metadata.
    body = {
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

    try:
        client.indices.create(index=index, body=body)
        print(f"created vector index {index!r}")
    except Exception as exc:  # noqa: BLE001
        if "resource_already_exists_exception" in str(exc):
            print(f"vector index {index!r} already exists — nothing to do")
            return 0
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
