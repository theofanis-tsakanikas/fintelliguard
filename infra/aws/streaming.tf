# =============================================================================
# The private streaming path: Databricks classic compute (in THIS customer-managed
# VPC) reaches MSK over the VPC — no NCC, no PrivateLink, no peering. The workspace
# data plane and the MSK brokers already share `aws_vpc.main` and its private
# subnets; the only things missing were (1) the security-group opening between them
# and (2) an instance profile so a Spark cluster can authenticate to MSK with IAM.
#
# All of this is additive and free when `enable_msk = false`: the instance profile
# costs nothing, and the SG rules reference security groups that always exist. The
# brokers they authorise simply are not there until MSK is turned on.
#
# Why NOT NCC/PrivateLink (the pattern the sibling governance platform uses): that
# is for SERVERLESS compute, whose data plane lives in Databricks' own account and
# must tunnel in. Our streaming consumer is CLASSIC compute in our own VPC, next to
# the brokers, and Kafka's multi-broker advertised-listener addressing resolves
# natively over in-VPC routing. PrivateLink would fight that; a security-group rule
# is the right tool. See docs/adr — classic + in-VPC.
# =============================================================================

# Databricks launches cluster nodes as EC2 instances; to let them assume the MSK
# IAM role it must be exposed as an instance profile. `aws_iam_role.msk_access`
# already trusts ec2.amazonaws.com and already carries the least-privilege
# kafka-cluster:* grants (Connect/DescribeCluster + topic R/W + consumer groups),
# so the instance profile is a thin wrapper — no new permissions minted here.
resource "aws_iam_instance_profile" "msk_access" {
  name = "${local.name}-msk-access"
  role = aws_iam_role.msk_access.name
  tags = { Name = "${local.name}-msk-access" }
}

# =============================================================================
# The in-VPC generator: the payment-gateway stand-in. It runs as a Fargate task inside
# THIS VPC (a private MSK has no public endpoint, so a laptop producer cannot reach it),
# authenticates to MSK with IAM (no stored secret), and produces the transaction stream
# the Databricks consumer scores. It is a demo DRIVER, not a standing service: there is no
# aws_ecs_service, so nothing runs 24/7 — the operator starts it with `aws ecs run-task`
# for the recording window and stops it after (build-once → prove → destroy).
#
# The security group is always created (SGs are free); everything that costs money — the
# ECR repo, the ECS cluster, the task definition and its roles — is gated by enable_msk,
# because a generator with no brokers to produce to is pure waste.
# =============================================================================

# Producer-only: egress to the brokers (9098) and to AWS APIs (443, for the IAM token
# signer's STS call and the ECR image pull via the NAT). No ingress. CIDR egress to 9098
# rather than the MSK SG, for the same no-cycle reason as the data-plane SG.
resource "aws_security_group" "generator" {
  name        = "${local.name}-generator"
  description = "In-VPC Fargate transaction generator to MSK (IAM SASL)"
  vpc_id      = aws_vpc.main.id

  egress {
    description = "Kafka IAM SASL to the in-VPC MSK brokers"
    from_port   = 9098
    to_port     = 9098
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    description = "HTTPS to AWS APIs (STS token signing, ECR image pull)"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-generator-sg" }
}

# ---- The container image registry (gated) -----------------------------------
resource "aws_ecr_repository" "generator" {
  count = var.enable_msk ? 1 : 0

  name = "${local.name}-generator"
  # MUTABLE, deliberately: the streaming stage rebuilds and re-pushes the generator image under
  # the `:latest` tag every run, which immutable tags forbid. This is a demo artifact, not a
  # released image whose provenance must be pinned.
  #checkov:skip=CKV_AWS_51:generator image is re-pushed under :latest each run; immutable tags block that
  image_tag_mutability = "MUTABLE"
  force_delete         = true # a demo image; let `destroy` clean the repo without manual purge

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.main.arn
  }

  tags = { Name = "${local.name}-generator" }
}

# ---- ECS cluster + logging (gated) ------------------------------------------
resource "aws_ecs_cluster" "streaming" {
  count = var.enable_msk ? 1 : 0

  name = "${local.name}-streaming"

  # Container Insights on: the generator's task metrics/logs go to CloudWatch, consistent with
  # the VPC flow logs and MSK broker logs — the estate records what runs at every boundary.
  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = { Name = "${local.name}-streaming" }
}

resource "aws_cloudwatch_log_group" "generator" {
  count = var.enable_msk ? 1 : 0

  name              = "/ecs/${local.name}-generator"
  retention_in_days = var.flow_log_retention_days
  kms_key_id        = aws_kms_key.main.arn

  tags = { Name = "${local.name}-generator-logs" }
}

# ---- ECS task execution role: pull the image, write logs (gated) ------------
data "aws_iam_policy_document" "ecs_execution_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.${local.dns_suffix}"]
    }
  }
}

resource "aws_iam_role" "ecs_execution" {
  count = var.enable_msk ? 1 : 0

  name               = "${local.name}-generator-exec"
  assume_role_policy = data.aws_iam_policy_document.ecs_execution_assume.json
  tags               = { Name = "${local.name}-generator-exec" }
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  count = var.enable_msk ? 1 : 0

  role       = aws_iam_role.ecs_execution[0].name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# The execution role also decrypts the CMK-encrypted ECR layers and log group.
data "aws_iam_policy_document" "ecs_execution_kms" {
  statement {
    sid       = "DecryptImageAndLogs"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = [aws_kms_key.main.arn]
  }
}

resource "aws_iam_role_policy" "ecs_execution_kms" {
  count = var.enable_msk ? 1 : 0

  name   = "generator-exec-kms"
  role   = aws_iam_role.ecs_execution[0].id
  policy = data.aws_iam_policy_document.ecs_execution_kms.json
}

# ---- The task definition (gated) --------------------------------------------
# task_role_arn is the MSK access role (the same least-privilege kafka-cluster grants the
# instance profile wraps): the container assumes it and the IAM token signer produces to MSK.
# The image tag is resolved to whatever was pushed; the container runs the simulator producing
# to the private brokers with SASL_SSL/OAUTHBEARER.
resource "aws_ecs_task_definition" "generator" {
  count = var.enable_msk ? 1 : 0

  family                   = "${local.name}-generator"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_execution[0].arn
  task_role_arn            = aws_iam_role.msk_access.arn

  container_definitions = jsonencode([
    {
      name      = "generator"
      image     = "${aws_ecr_repository.generator[0].repository_url}:${var.generator_image_tag}"
      essential = true
      command   = ["python", "-m", "simulator", "--sink", "kafka", "--no-ground-truth", "--rate", "30"]
      environment = [
        { name = "KAFKA_BOOTSTRAP_SERVERS", value = one(aws_msk_cluster.this[*].bootstrap_brokers_sasl_iam) },
        { name = "KAFKA_TOPIC", value = "txn.raw" },
        { name = "KAFKA_SECURITY_PROTOCOL", value = "SASL_SSL" },
        { name = "KAFKA_SASL_MECHANISM", value = "OAUTHBEARER" },
        { name = "AWS_REGION", value = local.region },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.generator[0].name
          "awslogs-region"        = local.region
          "awslogs-stream-prefix" = "generator"
        }
      }
    }
  ])

  tags = { Name = "${local.name}-generator" }
}
