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

# ALLOWLIST. The first version asserted `not permission.endswith("*")` — it blacklisted the
# SYNTAX of the finding rather than the CAPABILITY the finding names, so this shipped green:
#
#     Permission = ["aoss:DeleteDocument", "aoss:DeleteIndex"]   # no trailing *
#     Principal  = ["*"]                                          # every principal
#     Resource   = ["collection/*"]                               # every collection
#
# The test's own docstring said the harm of `aoss:*` is "deleting the collection the
# verdicts are grounded in". `aoss:DeleteIndex` does exactly that and has no trailing star.
# Enumerating what ingestion NEEDS is short; enumerating what it must not do is endless.
INGESTION_PERMISSIONS = {
    "aoss:CreateCollectionItems",
    "aoss:DescribeCollectionItems",
    "aoss:UpdateCollectionItems",
    "aoss:CreateIndex",
    "aoss:DescribeIndex",
    "aoss:UpdateIndex",
    "aoss:ReadDocument",
    "aoss:WriteDocument",
}


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


def test_the_kb_role_holds_only_the_permissions_ingestion_needs(model):
    """An allowlist: anything not on it — including `aoss:DeleteIndex` — fails.

    `aoss:*` includes destroying the corpus every verdict is grounded in. So does
    `aoss:DeleteIndex`, which has no trailing star and sailed past the blacklist that
    replaced it.
    """
    for rule in _policy(model, ACCESS_POLICY, "kb"):
        for entry in rule["Rules"]:
            granted = set(entry["Permission"])
            extra = sorted(granted - INGESTION_PERMISSIONS)
            assert not extra, (
                f"{entry['ResourceType']} grants {extra}, which ingestion does not need. "
                f"Allowed: {sorted(INGESTION_PERMISSIONS)}"
            )


def test_the_kb_data_policy_names_a_principal_and_a_scoped_resource(model):
    """Nothing checked either, so `Principal = ["*"]` and `Resource = ["collection/*"]` were
    invisible — every principal in the account with read/write on the regulatory corpus, and
    the scope widened to every collection.

    A least-privilege claim that never checks WHO or WHAT is a claim about verbs only.
    """
    for rule in _policy(model, ACCESS_POLICY, "kb"):
        principals = rule.get("Principal", [])
        assert principals, "the data access policy names no principal"
        for principal in principals:
            assert principal != "*", "the KB data policy grants to EVERY principal in the account"
            ref = model.resolve(principal)
            # A principal must be a NAMED IAM role — either one this layer declares, or the
            # deploying identity resolved from its assumed-role session
            # (`aws_iam_session_context.issuer_arn`, which yields exactly a role arn). The
            # deployer has to be here: AOSS authorises the data plane through this policy and
            # not through IAM, so the identity that creates the vector index is rejected with
            # a bare 401 unless it is named. What stays banned is a wildcard or a principal
            # that is not a role at all.
            assert ref.type == "aws_iam_role" or ref.name == "aws_iam_session_context", (
                f"{ref.address} is neither a declared IAM role nor the resolved deployer"
            )

        for entry in rule["Rules"]:
            for resource in entry["Resource"]:
                assert not resource.startswith(("collection/*", "index/*")), (
                    f"{resource!r} scopes to every collection in the account, not ours"
                )
                assert "${local.collection_name}" in resource, (
                    f"{resource!r} does not name this layer's collection — it is either "
                    "wider than intended or points somewhere unmanaged"
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
