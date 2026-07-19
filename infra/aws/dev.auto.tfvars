# Non-secret deployment configuration for `dev`, auto-loaded by Terraform (including in
# CI, which never sees the gitignored terraform.tfvars). Credentials NEVER belong here.

# Provision the real managed Kafka cluster rather than running Kafka locally. This is the
# architecture as described, and avoids a second deploy to swap it in later. Cost is
# ~$0.11/hour; the part that actually hurts is ~30 extra minutes on every apply/destroy.
enable_msk = true
