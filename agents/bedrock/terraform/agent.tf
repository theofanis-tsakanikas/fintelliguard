# The Tier-2 fraud-investigator agent + the FraudScoring action group + KB association.

resource "aws_bedrockagent_agent" "this" {
  agent_name                  = "${local.name}-fraud-investigator"
  agent_resource_role_arn     = aws_iam_role.agent.arn
  foundation_model            = var.foundation_model
  instruction                 = file("${path.module}/../instructions/fraud_investigator_v1.md")
  idle_session_ttl_in_seconds = 600
  description                 = "Tier-2 compliance-verdict agent: scores via get_fraud_score, grounds in AML/PSD2."

  # The regulated-output control. Without this binding the guardrail still EXISTS — it just
  # never runs, and every verdict ships unfiltered while the console shows a healthy policy.
  # That is not hypothetical: this block was missing, and no test noticed, because the tests
  # asserted the guardrail was *declared* rather than *attached*.
  # `tests/agents/bedrock/guardrails/test_guardrail_attachment.py` now proves the binding
  # resolves to the guardrail defined in `guardrail.tf`.
  guardrail_configuration = [{
    guardrail_identifier = aws_bedrock_guardrail.this.guardrail_id
    guardrail_version    = aws_bedrock_guardrail_version.this.version
  }]

  tags = { Name = "${local.name}-fraud-investigator" }
}

resource "aws_bedrockagent_agent_action_group" "fraud_scoring" {
  action_group_name          = "FraudScoring"
  agent_id                   = aws_bedrockagent_agent.this.agent_id
  agent_version              = "DRAFT"
  skip_resource_in_use_check = true

  action_group_executor {
    lambda = aws_lambda_function.fraud_scoring.arn
  }

  function_schema {
    member_functions {
      functions {
        name        = "get_fraud_score"
        description = "Return fraud_score, threshold, decision_hint and top contributing features for a transaction."

        parameters {
          map_block_key = "transaction_id"
          type          = "string"
          description   = "The transaction identifier."
          required      = true
        }

        parameters {
          map_block_key = "card_hash"
          type          = "string"
          description   = "The hashed card identifier (online-feature lookup key)."
          required      = true
        }
      }
    }
  }
}

resource "aws_bedrockagent_agent_knowledge_base_association" "this" {
  agent_id             = aws_bedrockagent_agent.this.agent_id
  knowledge_base_id    = aws_bedrockagent_knowledge_base.this.id
  agent_version        = "DRAFT"
  knowledge_base_state = "ENABLED"
  description          = "Ground every verdict in retrieved AML/PSD2 regulatory text."
}

# Deployable alias clients invoke; depends on the action group + KB association.
resource "aws_bedrockagent_agent_alias" "live" {
  agent_alias_name = "live"
  agent_id         = aws_bedrockagent_agent.this.agent_id
  description      = "Live alias for the fraud-investigator agent."

  depends_on = [
    aws_bedrockagent_agent_action_group.fraud_scoring,
    aws_bedrockagent_agent_knowledge_base_association.this,
    # A policy change must be live on the alias clients invoke, not just on the agent.
    aws_bedrock_guardrail_version.this,
  ]

  tags = { Name = "${local.name}-fraud-investigator-live" }
}
