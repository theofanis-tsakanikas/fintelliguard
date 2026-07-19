# Non-secret deployment configuration for `dev`, auto-loaded by Terraform (including in
# CI, which never sees the gitignored terraform.tfvars). Credentials NEVER belong here.

# Provision the real managed Kafka cluster rather than running Kafka locally. This is the
# architecture as described, and avoids a second deploy to swap it in later. Cost is
# ~$0.11/hour; the part that actually hurts is ~30 extra minutes on every apply/destroy.
enable_msk = true

# Purge deleted secrets IMMEDIATELY instead of parking them in a recovery window.
#
# Secrets Manager refuses to create a secret whose name is still scheduled for deletion:
#
#     InvalidRequestException: You can't create this secret because a secret with this
#     name is already scheduled for deletion.
#
# With the 7-day default, the dev estate could be destroyed and then NOT rebuilt for a
# week — a build/destroy cycle that only works once. Deploy run 29706126775 hit exactly
# that, and only after MSK had spent 26 minutes creating.
#
# 0 is right HERE and wrong in general: these are empty KMS-encrypted placeholders whose
# values are injected out of band and never live in Terraform, so there is nothing to
# recover. The variable keeps its 7-day default for environments where a deleted secret
# is worth a grace period.
secret_recovery_window_days = 0
