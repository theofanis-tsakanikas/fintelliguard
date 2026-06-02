# ml/serving/

Mosaic AI **Model Serving** endpoint config for the XGBoost scorer — REST, autoscaling,
<50 ms target.

This endpoint is the **cross-cloud contract**: Bedrock reaches the model ONLY through
`get_fraud_score()` against it, via a **private VPC endpoint, never public**. Bedrock
never reads Delta tables or Mosaic internals. Deployed via `infra/bundles/`.
