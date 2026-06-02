# =============================================================================
# VPC + subnets across `az_count` AZs, single NAT (cost), interface VPC endpoints
# for private AWS access, and least-privilege security groups.
# =============================================================================

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true # required for interface-endpoint private DNS

  tags = { Name = "${local.name}-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name}-igw" }
}

# ---- Subnets ----------------------------------------------------------------
resource "aws_subnet" "public" {
  count                   = var.az_count
  vpc_id                  = aws_vpc.main.id
  cidr_block              = var.public_subnet_cidrs[count.index]
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = true

  tags = { Name = "${local.name}-public-${local.azs[count.index]}" }
}

resource "aws_subnet" "private" {
  count             = var.az_count
  vpc_id            = aws_vpc.main.id
  cidr_block        = var.private_subnet_cidrs[count.index]
  availability_zone = local.azs[count.index]

  tags = { Name = "${local.name}-private-${local.azs[count.index]}" }
}

# ---- NAT (single, in the first public subnet — controls dev cost) -----------
resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = { Name = "${local.name}-nat-eip" }
}

resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id
  tags          = { Name = "${local.name}-nat" }

  depends_on = [aws_internet_gateway.main]
}

# ---- Route tables -----------------------------------------------------------
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${local.name}-public-rt" }
}

resource "aws_route_table_association" "public" {
  count          = var.az_count
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# One shared private route table — both private subnets egress via the single NAT.
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }

  tags = { Name = "${local.name}-private-rt" }
}

resource "aws_route_table_association" "private" {
  count          = var.az_count
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# =============================================================================
# Security groups (created with NO implicit egress — rules are explicit)
# =============================================================================

# Interface VPC endpoints: accept HTTPS only from inside the VPC.
resource "aws_security_group" "endpoints" {
  name        = "${local.name}-vpce"
  description = "HTTPS to interface VPC endpoints from within the VPC"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTPS from VPC"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  tags = { Name = "${local.name}-vpce-sg" }
}

# Lambda action-group (calls Mosaic Model Serving + AWS APIs): egress HTTPS only.
resource "aws_security_group" "lambda" {
  name        = "${local.name}-lambda"
  description = "Lambda action-group egress to Mosaic endpoint and AWS APIs"
  vpc_id      = aws_vpc.main.id

  egress {
    description = "HTTPS out (Databricks serving + interface endpoints)"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-lambda-sg" }
}

# MSK brokers: Kafka IAM/TLS ports from the Lambda SG and from brokers themselves.
resource "aws_security_group" "msk" {
  name        = "${local.name}-msk"
  description = "MSK broker access (IAM SASL / TLS) from in-VPC clients"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Kafka IAM SASL"
    from_port       = 9098
    to_port         = 9098
    protocol        = "tcp"
    security_groups = [aws_security_group.lambda.id]
    self            = true
  }

  ingress {
    description     = "Kafka TLS"
    from_port       = 9094
    to_port         = 9094
    protocol        = "tcp"
    security_groups = [aws_security_group.lambda.id]
    self            = true
  }

  egress {
    description = "Inter-broker traffic within the VPC"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [var.vpc_cidr]
  }

  tags = { Name = "${local.name}-msk-sg" }
}

# Databricks data-plane SG for the customer-managed VPC with back-end PrivateLink
# (secure cluster connectivity). Rules follow the Databricks customer-managed VPC docs:
#   - intra-SG all-traffic ingress AND egress (cluster node-to-node on all ports)
#   - egress 443  : control plane / SCC relay / cloud APIs / artifact + log storage
#   - egress 3306 : regional Databricks metastore
#   - egress 6666 : back-end PrivateLink secure cluster connectivity
#   - egress 8443-8451 : internal services (8443 control-plane API, 8444 UC lineage,
#                        8445, 8446-8451 reserved) — contiguous range per docs
# Omitted as not applicable here (per docs): 53 (custom DNS), 2443 (FIPS/compliance
# profile), 5432 (Lakebase). Add them if those features are enabled.
resource "aws_security_group" "databricks_data_plane" {
  name        = "${local.name}-databricks-dataplane"
  description = "Databricks customer-managed VPC data-plane SG (SCC / back-end PrivateLink)"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "Intra-SG: all traffic between cluster nodes in this SG"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    self        = true
  }

  egress {
    description = "Intra-SG: all traffic between cluster nodes in this SG"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    self        = true
  }

  egress {
    description = "Control plane / SCC relay / cloud APIs / artifact + log storage"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "Regional Databricks metastore"
    from_port   = 3306
    to_port     = 3306
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "Back-end PrivateLink secure cluster connectivity"
    from_port   = 6666
    to_port     = 6666
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "Databricks internal services over PrivateLink (8443-8451)"
    from_port   = 8443
    to_port     = 8451
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-databricks-dataplane-sg" }
}

# =============================================================================
# Interface VPC endpoints — keep Secrets Manager / KMS traffic off the internet,
# and (optionally) reach Mosaic Model Serving over Databricks PrivateLink.
# =============================================================================

locals {
  interface_endpoints = toset(["secretsmanager", "kms"])
}

resource "aws_vpc_endpoint" "aws" {
  for_each = local.interface_endpoints

  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${local.region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true

  tags = { Name = "${local.name}-vpce-${each.key}" }
}

# Databricks PrivateLink endpoint for private Mosaic Model Serving reach.
# Created only once the PrivateLink service name is known (default: not yet).
resource "aws_vpc_endpoint" "databricks_privatelink" {
  count = var.databricks_privatelink_service_name != "" ? 1 : 0

  vpc_id              = aws_vpc.main.id
  service_name        = var.databricks_privatelink_service_name
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = false

  tags = { Name = "${local.name}-vpce-databricks" }
}
