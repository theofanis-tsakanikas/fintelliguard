# Knowledge Base: S3 corpus (AML/PSD2 docs) -> OpenSearch Serverless vector store.

# ---- Corpus bucket (KMS-encrypted, private) ---------------------------------
resource "aws_s3_bucket" "kb_docs" {
  bucket = "${local.name}-kb-docs-${local.account_id}"
  tags   = { Name = "${local.name}-kb-docs" }
}

resource "aws_s3_bucket_public_access_block" "kb_docs" {
  bucket                  = aws_s3_bucket.kb_docs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "kb_docs" {
  bucket = aws_s3_bucket.kb_docs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = local.aws.kms_key_arn
    }
    bucket_key_enabled = true
  }
}

# Versioning on the regulatory corpus is a compliance control, not a convenience: a verdict
# cites the text that was retrieved when it was issued, so the retrieved text has to remain
# reconstructible. The raw bucket has this; this one did not.
resource "aws_s3_bucket_versioning" "kb_docs" {
  bucket = aws_s3_bucket.kb_docs.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "kb_docs" {
  bucket = aws_s3_bucket.kb_docs.id
  rule {
    id     = "expire-noncurrent-corpus-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 365
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
  depends_on = [aws_s3_bucket_versioning.kb_docs]
}

# ---- OpenSearch Serverless vector collection --------------------------------
resource "aws_opensearchserverless_security_policy" "encryption" {
  name = "${local.name}-kb-enc"
  type = "encryption"
  policy = jsonencode({
    Rules = [{ Resource = ["collection/${local.collection_name}"], ResourceType = "collection" }]
    # The customer-managed key, not the AWS-owned one. This collection holds the embedded
    # regulatory corpus that every Tier-2 verdict is grounded in; a CMK exists two layers
    # away and everything else in the project uses it.
    AWSOwnedKey = false
    KmsARN      = local.aws.kms_key_arn
  })
}

# The private door into the collection. Without it, `AllowFromPublic = false` would lock
# out the Bedrock KB along with everyone else.
resource "aws_opensearchserverless_vpc_endpoint" "kb" {
  name               = "${local.name}-kb-vpce"
  vpc_id             = local.aws.vpc_id
  subnet_ids         = local.aws.private_subnet_ids
  security_group_ids = [local.aws.endpoints_security_group_id]
}

resource "aws_opensearchserverless_security_policy" "network" {
  name = "${local.name}-kb-net"
  type = "network"
  # Was `AllowFromPublic = true` — in a file whose own header says "private", on the
  # collection holding the AML/PSD2 corpus and its embeddings. SigV4 and the data access
  # policy still applied, so this was not an open bucket, but it removed the network
  # control plane entirely and contradicted the posture CLAUDE.md and the README state.
  policy = jsonencode([{
    Rules = [
      { Resource = ["collection/${local.collection_name}"], ResourceType = "collection" },
      { Resource = ["collection/${local.collection_name}"], ResourceType = "dashboard" },
    ]
    AllowFromPublic = false
    SourceVPCEs     = [aws_opensearchserverless_vpc_endpoint.kb.id]
  }])
}

resource "aws_opensearchserverless_collection" "kb" {
  name = local.collection_name
  type = "VECTORSEARCH"

  depends_on = [
    aws_opensearchserverless_security_policy.encryption,
    aws_opensearchserverless_security_policy.network,
  ]

  tags = { Name = local.collection_name }
}

resource "aws_opensearchserverless_access_policy" "kb" {
  name = "${local.name}-kb-acc"
  type = "data"
  # Was `Permission = ["aoss:*"]` on both — directly under `iam.tf`'s "No wildcards in
  # authored policies (ARNs are scoped)". `aoss:*` on the data plane includes deleting the
  # collection and its indices; the KB ingestion role needs to read, write and manage its
  # own index, and nothing more.
  policy = jsonencode([{
    Rules = [
      {
        Resource     = ["collection/${local.collection_name}"]
        Permission   = ["aoss:CreateCollectionItems", "aoss:DescribeCollectionItems", "aoss:UpdateCollectionItems"]
        ResourceType = "collection"
      },
      {
        Resource     = ["index/${local.collection_name}/*"]
        Permission   = ["aoss:CreateIndex", "aoss:DescribeIndex", "aoss:UpdateIndex", "aoss:ReadDocument", "aoss:WriteDocument"]
        ResourceType = "index"
      },
    ]
    Principal = [aws_iam_role.kb.arn]
  }])
}

# ---- Bedrock Knowledge Base + S3 data source --------------------------------
resource "aws_bedrockagent_knowledge_base" "this" {
  name     = "${local.name}-regulations"
  role_arn = aws_iam_role.kb.arn

  knowledge_base_configuration {
    type = "VECTOR"
    vector_knowledge_base_configuration {
      embedding_model_arn = local.embedding_model_arn
    }
  }

  storage_configuration {
    type = "OPENSEARCH_SERVERLESS"
    opensearch_serverless_configuration {
      collection_arn    = aws_opensearchserverless_collection.kb.arn
      vector_index_name = var.kb_vector_index_name
      field_mapping {
        vector_field   = "embedding"
        text_field     = "text"
        metadata_field = "metadata"
      }
    }
  }

  depends_on = [aws_opensearchserverless_access_policy.kb]
}

resource "aws_bedrockagent_data_source" "regulations" {
  knowledge_base_id = aws_bedrockagent_knowledge_base.this.id
  name              = "regulatory-corpus"

  data_source_configuration {
    type = "S3"
    s3_configuration {
      bucket_arn = aws_s3_bucket.kb_docs.arn
    }
  }
}
