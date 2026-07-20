output "agent_id" {
  description = "Bedrock agent id."
  value       = aws_bedrockagent_agent.this.agent_id
}

output "agent_arn" {
  description = "Bedrock agent ARN."
  value       = aws_bedrockagent_agent.this.agent_arn
}

output "agent_alias_id" {
  description = "Live agent alias id (the invocation target)."
  value       = aws_bedrockagent_agent_alias.live.agent_alias_id
}

output "action_group_lambda_name" {
  description = "FraudScoring action-group Lambda name."
  value       = aws_lambda_function.fraud_scoring.function_name
}

output "knowledge_base_id" {
  description = "Regulatory Knowledge Base id."
  value       = aws_bedrockagent_knowledge_base.this.id
}

output "data_source_id" {
  description = <<-DESC
    Id of the S3 data source the corpus is ingested through. Needed by
    `python -m agents.bedrock.kb.ingest`, which starts and waits on the ingestion job:
    `start_ingestion_job` takes the data source id, not the bucket, and it was the one
    identifier the deploy could not resolve without a console visit.
  DESC
  value       = aws_bedrockagent_data_source.regulations.data_source_id
}

output "vector_collection_arn" {
  description = "OpenSearch Serverless vector collection ARN."
  value       = aws_opensearchserverless_collection.kb.arn
}

output "kb_docs_bucket" {
  description = "S3 bucket holding the regulatory corpus."
  value       = aws_s3_bucket.kb_docs.bucket
}

output "guardrail_id" {
  description = "Bedrock guardrail id."
  value       = aws_bedrock_guardrail.this.guardrail_id
}

output "guardrail_arn" {
  description = "Bedrock guardrail ARN."
  value       = aws_bedrock_guardrail.this.guardrail_arn
}
