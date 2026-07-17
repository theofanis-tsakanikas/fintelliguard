"""The regulatory Knowledge Base must be private and least-privileged.

Two findings this guards, both of which sat directly under comments asserting the
opposite:

  * `knowledge_base.tf` header says "private"; the network policy said
    `AllowFromPublic = true`, so the OpenSearch collection holding the AML/PSD2 corpus and
    its embeddings accepted connections from the public internet. SigV4 and the data
    access policy still applied — it was not an open bucket — but the network control plane
    was gone, and CLAUDE.md/README state privacy as fact.
  * `iam.tf:1` says "No wildcards in authored policies (ARNs are scoped)"; the data access
    policy granted `aoss:*` on the collection AND every index.

Neither was visible to any test, because both live inside `jsonencode(...)` — one opaque
string to a grep. `terraform_model.json_body` unwraps them into data.
"""

from __future__ import annotations

import pytest

from agents.bedrock.terraform_model import bedrock_model, json_body

SECURITY_POLICY = "aws_opensearchserverless_security_policy"
ACCESS_POLICY = "aws_opensearchserverless_access_policy"
VPC_ENDPOINT = "aws_opensearchserverless_vpc_endpoint"


@pytest.fixture(scope="module")
def model():
    return bedrock_model()


def _policy(model, rtype: str, name: str):
    body = model.get(rtype, name)
    assert body is not None, f"{rtype}.{name} is gone"
    return json_body(body["policy"])


def test_the_regulatory_corpus_is_not_reachable_from_the_public_internet(model):
    for rule in _policy(model, SECURITY_POLICY, "network"):
        assert rule.get("AllowFromPublic") is False, (
            "the vector store holding the regulatory corpus accepts public network "
            "connections — the only thing between it and the internet is credentials"
        )


def test_the_private_network_rule_points_at_a_vpc_endpoint_that_exists(model):
    """`AllowFromPublic = false` without a working VPC endpoint locks out the KB itself.

    A dangling `SourceVPCEs` is worse than the public policy it replaced: it reads as
    hardened and it does not work.
    """
    for rule in _policy(model, SECURITY_POLICY, "network"):
        sources = rule.get("SourceVPCEs")
        assert sources, "private network policy with no source VPC endpoint"
        for source in sources:
            ref = model.resolve(source)
            assert ref.type == VPC_ENDPOINT, f"{ref.address} is not a VPC endpoint"


def test_the_kb_role_holds_no_wildcard_data_plane_permissions(model):
    """`aoss:*` includes deleting the collection the verdicts are grounded in."""
    for rule in _policy(model, ACCESS_POLICY, "kb"):
        for entry in rule["Rules"]:
            for permission in entry["Permission"]:
                assert not permission.endswith("*"), (
                    f"{entry['ResourceType']} grants {permission} — a data-plane wildcard, "
                    "under a comment promising none"
                )


def test_the_vector_collection_is_encrypted_with_the_customer_managed_key(model):
    """An AWS-owned key on the corpus, while a CMK protects everything else, is drift."""
    policy = _policy(model, SECURITY_POLICY, "encryption")
    assert policy.get("AWSOwnedKey") is False, (
        "the vector store uses an AWS-owned key while the rest of the project is on a "
        "customer-managed key"
    )
    # The key comes from the aws layer's remote state, so it is a `local`, not a resource
    # in this layer — there is nothing here to resolve it against. Asserting it names the
    # shared CMK is as far as this layer's own files can honestly go.
    assert policy.get("KmsARN") == "${local.aws.kms_key_arn}", (
        f"AWSOwnedKey is false but the key is {policy.get('KmsARN')!r}, not the project CMK"
    )
