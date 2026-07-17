# Guardrail on regulated Tier-2 output: PII redaction, denied topics, grounding check.

resource "aws_bedrock_guardrail" "this" {
  name                      = "${local.name}-guardrail"
  description               = "PII redaction, denied topics, and grounding/hallucination checks for verdicts."
  blocked_input_messaging   = "This request cannot be processed by the fraud-compliance agent."
  blocked_outputs_messaging = "This response was withheld pending compliance review."

  # PII redaction (inputs and outputs).
  sensitive_information_policy_config {
    pii_entities_config {
      type   = "CREDIT_DEBIT_CARD_NUMBER"
      action = "ANONYMIZE"
    }
    pii_entities_config {
      type   = "NAME"
      action = "ANONYMIZE"
    }
    pii_entities_config {
      type   = "EMAIL"
      action = "ANONYMIZE"
    }
  }

  # Denied topics — the agent issues compliance verdicts, not financial advice.
  topic_policy_config {
    topics_config {
      name       = "investment-advice"
      definition = "Providing investment, trading, or personal financial advice to a customer."
      type       = "DENY"
      examples   = ["Should I invest in this?", "What stocks should I buy?"]
    }
  }

  # Content filters.
  content_policy_config {
    filters_config {
      type            = "HATE"
      input_strength  = "HIGH"
      output_strength = "HIGH"
    }
    filters_config {
      type            = "PROMPT_ATTACK"
      input_strength  = "HIGH"
      output_strength = "NONE"
    }
  }

  # Grounding / hallucination guard for the generated verdict.
  contextual_grounding_policy_config {
    filters_config {
      type      = "GROUNDING"
      threshold = 0.75
    }
    filters_config {
      type      = "RELEVANCE"
      threshold = 0.75
    }
  }

  tags = { Name = "${local.name}-guardrail" }
}

# A fingerprint of the policy above. This is what makes the version track it.
#
# `aws_bedrock_guardrail_version` has NO argument derived from the policy body — only
# `guardrail_arn`, which does not change when the policy does. So the resource was never
# replaced: edit any filter, threshold or PII entity and Terraform saw no diff, the agent
# stayed pinned to version 1 forever, and DRAFT drifted underneath it. The comment here
# claimed "every apply that changes the policy mints a new version". It was the exact
# inverse of the truth, and it was a comment denying a hole — the pattern this layer's own
# commit history names.
#
# Hashing the policy into `description` gives the resource something that DOES change, and
# `replace_triggered_by` turns that change into a new version.
locals {
  guardrail_policy_fingerprint = sha256(
    jsonencode({
      pii       = aws_bedrock_guardrail.this.sensitive_information_policy_config
      topics    = aws_bedrock_guardrail.this.topic_policy_config
      content   = aws_bedrock_guardrail.this.content_policy_config
      grounding = aws_bedrock_guardrail.this.contextual_grounding_policy_config
      blocked   = aws_bedrock_guardrail.this.blocked_outputs_messaging
    })
  )
}

# An immutable, numbered snapshot of the policy above.
#
# The agent binds to THIS, never to DRAFT. DRAFT mutates in place, so an agent bound to it
# has no stable answer to "which policy was in force when this verdict was issued?" — the
# question an auditor asks first.
resource "aws_bedrock_guardrail_version" "this" {
  guardrail_arn = aws_bedrock_guardrail.this.guardrail_arn

  # The fingerprint rides in the description so the version is self-identifying in the
  # console: an auditor reading version 3 can tell which policy body produced it.
  description = "Policy snapshot ${substr(local.guardrail_policy_fingerprint, 0, 12)} bound to the fraud-investigator agent."

  lifecycle {
    # The whole point: a policy change replaces this resource, `agent.tf` re-points the
    # binding, and every decision stays attributable to a frozen set of rules.
    replace_triggered_by = [aws_bedrock_guardrail.this]
  }

  # Keep superseded versions: a destroyed version cannot be produced as evidence for a
  # decision that was made while it was in force.
  skip_destroy = true
}
