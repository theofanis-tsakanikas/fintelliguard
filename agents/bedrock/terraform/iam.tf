# Least-privilege roles for the Bedrock agent and the Knowledge Base. No wildcards in
# authored policies (ARNs are scoped; account/region come from data sources).

# ---- Agent execution role ----------------------------------------------------
data "aws_iam_policy_document" "agent_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "agent" {
  name               = "${local.name}-bedrock-agent"
  assume_role_policy = data.aws_iam_policy_document.agent_assume.json
  tags               = { Name = "${local.name}-bedrock-agent" }
}

data "aws_iam_policy_document" "agent" {
  statement {
    sid     = "InvokeFoundationModel"
    actions = ["bedrock:InvokeModel"]
    # The inference profile AND the base model in every region it can route to — invoking
    # through a cross-region profile is denied without InvokeModel on both.
    resources = local.foundation_model_invoke_arns
  }

  statement {
    sid       = "RetrieveFromKnowledgeBase"
    actions   = ["bedrock:Retrieve"]
    resources = [aws_bedrockagent_knowledge_base.this.arn]
  }
}

resource "aws_iam_role_policy" "agent" {
  name   = "agent-access"
  role   = aws_iam_role.agent.id
  policy = data.aws_iam_policy_document.agent.json
}

# ---- Knowledge Base role -----------------------------------------------------
data "aws_iam_policy_document" "kb_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "kb" {
  name               = "${local.name}-bedrock-kb"
  assume_role_policy = data.aws_iam_policy_document.kb_assume.json
  tags               = { Name = "${local.name}-bedrock-kb" }
}

data "aws_iam_policy_document" "kb" {
  statement {
    sid       = "InvokeEmbeddingModel"
    actions   = ["bedrock:InvokeModel"]
    resources = [local.embedding_model_arn]
  }

  statement {
    sid       = "AccessVectorStore"
    actions   = ["aoss:APIAccessAll"]
    resources = [aws_opensearchserverless_collection.kb.arn]
  }

  statement {
    sid       = "ReadCorpusBucket"
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.kb_docs.arn, "${aws_s3_bucket.kb_docs.arn}/*"]
  }

  statement {
    sid       = "DecryptCorpus"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [local.aws.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "kb" {
  name   = "kb-access"
  role   = aws_iam_role.kb.id
  policy = data.aws_iam_policy_document.kb.json
}
