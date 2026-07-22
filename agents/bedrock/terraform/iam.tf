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
    sid = "InvokeFoundationModel"
    # BOTH the buffered and the STREAMING action: the Bedrock agent invokes its model with
    # response streaming, so InvokeModel alone is not enough — without
    # InvokeModelWithResponseStream the agent's execution is denied and InvokeAgent returns an
    # opaque accessDenied even though the CALLER is authorized (deploy run 29897999539; the
    # caller's InvokeAgent simulates as allowed, so the denial is the agent's own model call).
    actions = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    # The inference profile AND the base model in every region it can route to — invoking
    # through a cross-region profile is denied without InvokeModel on both.
    resources = local.foundation_model_invoke_arns
  }

  statement {
    sid       = "RetrieveFromKnowledgeBase"
    actions   = ["bedrock:Retrieve"]
    resources = [aws_bedrockagent_knowledge_base.this.arn]
  }

  # The agent APPLIES the attached guardrail (on input and output). Without ApplyGuardrail on
  # the guardrail ARN the agent's execution is denied at the very first step — the guardrail is
  # applied to the input before the model is ever called — and InvokeAgent returns an opaque
  # accessDenied while the caller is authorized (deploy run 29900921801; simulate confirmed the
  # role's ApplyGuardrail was implicitDeny). The guardrail binding is worthless without this.
  statement {
    sid       = "ApplyGuardrail"
    actions   = ["bedrock:ApplyGuardrail"]
    resources = [aws_bedrock_guardrail.this.guardrail_arn]
  }

  # The agent encrypts its session state with the estate CMK (customer_encryption_key_arn on
  # aws_bedrockagent_agent). Scoped to that one key.
  statement {
    sid = "SessionEncryptionWithCMK"
    actions = [
      "kms:Decrypt",
      "kms:GenerateDataKey",
      "kms:DescribeKey",
    ]
    resources = [local.aws.kms_key_arn]
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
