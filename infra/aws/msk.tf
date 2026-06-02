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

  tags = { Name = local.msk_cluster_name }
}
