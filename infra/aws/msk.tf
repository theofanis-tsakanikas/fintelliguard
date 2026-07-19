# MSK cluster — COST-GUARDED.
#
# enable_msk defaults to false, so `terraform apply` does NOT create MSK: dev uses local
# Kafka (Docker). Flip to true ONLY for integration testing / the final demo, then
# `destroy` (or flip back) when idle. MSK runs brokers 24/7 and is the single most
# expensive resource in this layer.
#
# Smallest viable config: one kafka.t3.small broker per AZ, 10 GB EBS each, IAM SASL
# auth, TLS in transit, CMK at rest.

resource "aws_msk_cluster" "this" {
  count = var.enable_msk ? 1 : 0

  cluster_name           = local.msk_cluster_name
  kafka_version          = var.msk_kafka_version
  number_of_broker_nodes = var.az_count

  broker_node_group_info {
    instance_type   = var.msk_broker_instance_type
    client_subnets  = aws_subnet.private[*].id
    security_groups = [aws_security_group.msk.id]

    storage_info {
      ebs_storage_info {
        volume_size = var.msk_broker_ebs_gb
      }
    }
  }

  client_authentication {
    sasl {
      iam = true
    }
  }

  encryption_info {
    encryption_at_rest_kms_key_arn = aws_kms_key.main.arn

    encryption_in_transit {
      client_broker = "TLS"
      in_cluster    = true
    }
  }

  # Broker logs to CloudWatch (CKV_AWS_80). Without this the cluster carries the whole
  # transaction stream and keeps no record of who connected, what was rejected, or why a
  # broker restarted — so a Kafka-side incident is unreconstructable, and the platform that
  # exists to produce an audit trail has a blind spot at its own ingestion boundary.
  #
  # Encrypted with the same CMK as everything else, and retained for the same window as the
  # VPC flow logs so a single incident lookback covers both network and broker evidence.
  logging_info {
    broker_logs {
      cloudwatch_logs {
        enabled   = true
        log_group = aws_cloudwatch_log_group.msk_broker[0].name
      }
    }
  }

  tags = { Name = local.msk_cluster_name }
}

resource "aws_cloudwatch_log_group" "msk_broker" {
  count = var.enable_msk ? 1 : 0

  name              = "/aws/msk/${local.name}/broker-logs"
  retention_in_days = var.flow_log_retention_days
  kms_key_id        = aws_kms_key.main.arn

  tags = { Name = "${local.name}-msk-broker-logs" }
}
