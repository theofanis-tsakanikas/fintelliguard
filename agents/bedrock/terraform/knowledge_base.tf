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

# ---- OpenSearch Serverless vector collection --------------------------------
resource "aws_opensearchserverless_security_policy" "encryption" {
  name = "${local.name}-kb-enc"
  type = "encryption"
  policy = jsonencode({
    Rules       = [{ Resource = ["collection/${local.collection_name}"], ResourceType = "collection" }]
    AWSOwnedKey = true
  })
}

resource "aws_opensearchserverless_security_policy" "network" {
  name = "${local.name}-kb-net"
  type = "network"
  policy = jsonencode([{
    Rules = [
      { Resource = ["collection/${local.collection_name}"], ResourceType = "collection" },
      { Resource = ["collection/${local.collection_name}"], ResourceType = "dashboard" },
    ]
    AllowFromPublic = true
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
  policy = jsonencode([{
    Rules = [
      { Resource = ["collection/${local.collection_name}"], Permission = ["aoss:*"], ResourceType = "collection" },
      { Resource = ["index/${local.collection_name}/*"], Permission = ["aoss:*"], ResourceType = "index" },
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
