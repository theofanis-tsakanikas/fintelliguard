# Non-secret deployment configuration for `dev`, auto-loaded by Terraform (including in CI,
# which never sees the gitignored terraform.tfvars). Credentials NEVER belong here.

# ENTERPRISE, not the PREMIUM default. Tier availability is a property of the DATABRICKS
# SUBSCRIPTION, not a preference: this account offers only ENTERPRISE, and requesting
# PREMIUM failed workspace creation outright ("Feature tier PREMIUM is unavailable for
# subscription ... which has available tiers: ENTERPRISE").
#
# ENTERPRISE is a superset of PREMIUM, so everything the project needs from the tier —
# Unity Catalog, cluster policies — is still available. Note it also bills at a higher
# per-DBU rate, which matters for how long the workspace is left running.
pricing_tier = "ENTERPRISE"
