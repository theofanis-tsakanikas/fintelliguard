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
