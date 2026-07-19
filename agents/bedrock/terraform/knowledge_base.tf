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
    # Two source paths, and BOTH are needed. The VPC endpoint is how our own principals
    # (ingestion, admin) reach the collection privately. `SourceServices = bedrock` is how
    # the Bedrock Knowledge Base reaches it — Bedrock queries from the Bedrock service
    # network, not from our VPC, so with `SourceVPCEs` alone the lockdown locked out the one
    # principal that has to get in, and KB creation/ingestion would fail.
    SourceServices = ["bedrock.amazonaws.com"]
    SourceVPCEs    = [aws_opensearchserverless_vpc_endpoint.kb.id]
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
    # Two principals, for two different jobs:
    #  - the KB ingestion role, which reads and writes documents at run time;
    #  - the DEPLOYING identity, which has to CREATE the index in the first place.
    #
    # Only the first was listed, so `create_kb_index.py` — running as the deploy role —
    # was in no data access policy at all and AOSS rejected it with `401, ''`. Nothing in
    # that message points at the access policy, and the IAM policy on the deploy role is
    # irrelevant here: AOSS authorises the data plane through ITS OWN policy, not IAM.
    Principal = [
      aws_iam_role.kb.arn,
      aws_iam_role.kb_index.arn,
      data.aws_iam_session_context.current.issuer_arn,
    ]
  }])
}

# ---- The vector index the KB requires to pre-exist --------------------------
#
# Bedrock does NOT create this index; it requires it to already exist with the exact field
# mapping below, and `aws_bedrockagent_knowledge_base` fails at apply if it does not. There
# is no first-class provider resource for an AOSS vector index — it is a data-plane object,
# created over the collection's HTTPS endpoint with SigV4.
#
# This used to be a `local-exec` running a script on the CI runner. It could never have
# worked: the network policy is `AllowFromPublic = false` with only the VPC endpoint and
# Bedrock as sources, and a GitHub-hosted runner is neither. AOSS refuses it at the network
# layer and answers `401` with an empty body — indistinguishable from an auth problem, which
# is why it survived a round of fixing the (also genuinely wrong) data access policy.
#
# Running it from a Lambda INSIDE the VPC is the fix that keeps the posture: the collection
# stays unreachable from the internet, and the one caller that must reach it does so through
# the same private endpoint Bedrock uses.

data "archive_file" "kb_index" {
  type        = "zip"
  source_dir  = "${path.module}/../kb_index_lambda"
  output_path = "${path.module}/build/kb_index_lambda.zip"
  excludes    = ["__pycache__", "build"]
}

data "aws_iam_policy_document" "kb_index_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "kb_index" {
  name               = "${local.name}-kb-index"
  assume_role_policy = data.aws_iam_policy_document.kb_index_assume.json
  tags               = { Name = "${local.name}-kb-index" }
}

# ENI management for the VPC attachment, and nothing else. `aoss:APIAccessAll` is the IAM
# half of data-plane access; the other half is naming this role in the data access policy
# below — AOSS requires BOTH, which is what makes a missing one look like an auth bug.
resource "aws_iam_role_policy" "kb_index" {
  name = "kb-index"
  role = aws_iam_role.kb_index.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "AossDataPlane"
        Effect   = "Allow"
        Action   = ["aoss:APIAccessAll"]
        Resource = [aws_opensearchserverless_collection.kb.arn]
      },
      {
        Sid      = "DeadLetterQueue"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = [aws_sqs_queue.kb_index_dlq.arn]
      },
      {
        Sid      = "EncryptWithTheEstateCMK"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey*"]
        Resource = [local.aws.kms_key_arn]
      },
    ]
  })
}

# The AWS-managed policy for Lambda VPC attachment, rather than hand-rolled ENI
# permissions. Hand-rolling them failed at CreateFunction:
#
#     InvalidParameterValueException: The provided execution role does not have
#     permissions to call CreateNetworkInterface on EC2
#
# because Lambda pre-flight-checks the role, and the `ec2:Vpc` condition added to satisfy
# checkov cannot be evaluated at that moment — there is no interface, and no VPC context,
# until the function exists. The managed policy is what Lambda's check expects, and it is
# also what infra/aws already uses for the action-group Lambda.
resource "aws_iam_role_policy_attachment" "kb_index_vpc" {
  role       = aws_iam_role.kb_index.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_cloudwatch_log_group" "kb_index" {
  name              = "/aws/lambda/${local.name}-kb-index"
  retention_in_days = var.lambda_log_retention_days
  kms_key_id        = local.aws.kms_key_arn
  tags              = { Name = "${local.name}-kb-index-logs" }
}

resource "aws_sqs_queue" "kb_index_dlq" {
  name                              = "${local.name}-kb-index-dlq"
  kms_master_key_id                 = local.aws.kms_key_arn
  kms_data_key_reuse_period_seconds = 300
  tags                              = { Name = "${local.name}-kb-index-dlq" }
}

resource "aws_lambda_function" "kb_index" {
  #checkov:skip=CKV_AWS_272:Needs a signer profile + signed artifact; archive_file emits unsigned zips. Same deferral as the action-group Lambda.
  function_name = "${local.name}-kb-index"
  role          = aws_iam_role.kb_index.arn
  runtime       = var.lambda_runtime
  handler       = "handler.lambda_handler"
  filename      = data.archive_file.kb_index.output_path

  source_code_hash = data.archive_file.kb_index.output_base64sha256

  # Covers the handler's 403 retry budget (~50s of backoff across 6 attempts) plus the
  # requests themselves. A 60s timeout would have killed the retry that exists to survive
  # AOSS policy propagation.
  timeout = 120

  # One invocation per apply — a concurrency of 1 is not a throttle, it is a statement that
  # two of these must never race to create the same index.
  reserved_concurrent_executions = 1

  kms_key_arn = local.aws.kms_key_arn

  dead_letter_config {
    target_arn = aws_sqs_queue.kb_index_dlq.arn
  }

  tracing_config {
    mode = "Active"
  }

  vpc_config {
    subnet_ids         = local.aws.private_subnet_ids
    security_group_ids = [local.aws.lambda_security_group_id]
  }

  environment {
    variables = {
      AOSS_ENDPOINT = aws_opensearchserverless_collection.kb.collection_endpoint
      INDEX_NAME    = var.kb_vector_index_name
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.kb_index,
    aws_iam_role_policy.kb_index,
    aws_iam_role_policy_attachment.kb_index_vpc,
  ]
  tags = { Name = "${local.name}-kb-index" }
}

# Runs at apply time. The handler is idempotent, so re-applying the layer with the index
# already present is a no-op rather than a failure.
resource "aws_lambda_invocation" "kb_index" {
  function_name = aws_lambda_function.kb_index.function_name
  input         = jsonencode({ index = var.kb_vector_index_name })

  depends_on = [aws_opensearchserverless_access_policy.kb]
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

  # The index must exist before the KB references it.
  depends_on = [
    aws_opensearchserverless_access_policy.kb,
    aws_lambda_invocation.kb_index,
  ]
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
