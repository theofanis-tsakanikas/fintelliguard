# Security

## Scope

This repository is a **portfolio reference implementation** of a regulated-AI system: real-time
fraud scoring, an LLM agent that drafts compliance verdicts under AML/PSD2, and an analyst copilot.
Because the domain is regulated, the security and Responsible-AI controls are the substance of the
project rather than a wrapper around it — which is why this file is longer than the usual template.

**No real cardholder data is processed.** Training uses the public **IEEE-CIS** competition dataset;
the live stream is a synthetic generator; the copilot's case corpus is `SYNTH-*` records. The PII
controls exist because the *shape* of the data is payment data and the platform is built as if it
were real.

**Nothing is currently deployed.** The estate is provisioned by one dispatch, exercised, captured
and destroyed; its resting state is "gone". Read this alongside
[What this does not do](README.md#what-this-does-not-do) in the README — nothing is claimed here
that the code does not do.

## Reporting a vulnerability

Open a [GitHub issue](https://github.com/theofanis-tsakanikas/fintelliguard/issues) for anything
non-sensitive. For something that should not be public, email the address on the
[GitHub profile](https://github.com/theofanis-tsakanikas) with `SECURITY` in the subject. There is
no bug bounty and no SLA; this is a personal project and a best-effort response is what it can
honestly promise.

Only `main` is supported. There are no maintained release branches.

---

## What is hardened

### Federation is pinned to subjects, not to a repository wildcard

Every cloud workflow authenticates over **GitHub OIDC** — there is no long-lived AWS key anywhere in
the repository. The deployer role's trust policy is built in
[`infra/aws/bootstrap/oidc.tf`](infra/aws/bootstrap/oidc.tf) from two explicit lists:

```
repo:<owner>/<repo>:ref:refs/heads/<branch>     for each deploy branch
repo:<owner>/<repo>:environment:<environment>   for each deploy environment
```

matched with `StringEquals`, and the file says so in a comment: *NOT `repo:.../*`*. A workflow on an
arbitrary branch cannot assume the role. This is the control most repositories get wrong, and it is
in code here rather than configured by hand.

Workflow permissions are minimal throughout — `contents: read`, with `id-token: write` only where
federation happens. `bootstrap`, `deploy` and `destroy` are `workflow_dispatch`-only and each targets
a GitHub Environment with reviewer protection; a merge to `main` cannot provision anything.

### One customer-managed key, rotated, over everything

A customer-managed **KMS CMK with `enable_key_rotation = true`** encrypts MSK at rest, the S3 data
lake (`sse_algorithm = "aws:kms"`), Secrets Manager, CloudWatch logs and the online-feature store.
The only `Resource = "*"` statements in the whole estate are inside the **KMS key policy**, where AWS
requires it to mean "this key" — and the file carries both an explanatory comment and a scoped
`checkov:skip` so the exception is deliberate and auditable rather than a lint suppression.

S3 additionally carries **versioning** and a full public-access block.

### The cross-cloud path is private and narrow

MSK enforces `encryption_in_transit { client_broker = "TLS", in_cluster = true }` with **SASL IAM**
authentication — there is no unauthenticated or plaintext listener. Bedrock reaches the model
**only** through `get_fraud_score()`: a VPC-internal Lambda on a least-privilege role that fetches
online features, calls the Mosaic endpoint over private connectivity with a Databricks OAuth token
pulled from Secrets Manager at runtime, and returns a fixed contract. **Bedrock never reads Delta,
never sees raw data, and never holds a standing credential.** Secrets Manager and KMS are reached
through VPC endpoints rather than the internet.

### Regulated-AI controls, enforced in CI

- **Guardrails** are bound to the agent at an **immutable version**, with PII redaction
  (`CREDIT_DEBIT_CARD_NUMBER`, `NAME`, `EMAIL` → `ANONYMIZE`), a denied topic, a prompt-attack
  filter and contextual grounding at 0.75. A test parses `guardrail.tf` for referential integrity,
  so removing a policy class fails the build.
- **A deterministic verdict gate** runs before any Tier-2 verdict reaches an analyst: schema,
  no-PII, grounding (cited provisions must be *in the retrieved context*), faithfulness (declared
  drivers must be a subset of the model's actual `top_features`), and direction — the agent may
  escalate but **may never soften** ([ADR-0006](docs/adr/0006-deterministic-verdict-gate.md)).
- **Decision records** are written for every scored transaction under a correlation id, carrying
  `model_version` and `guardrail_version`, and **refuse to be written** if they would carry raw PII
  — the EU AI Act Art. 12 record-keeping.
- **The governance documents are generated from the code**, and CI's `--check` fails if a committed
  byte drifts from the source.
- **`make gate-proof` attacks every gate**, planting a real violation and failing unless the real
  gate refuses it *for the right reason*. Each planted attack is a bug this repository actually
  shipped once.

### Secrets

`gitleaks` scans the **full git history** (`fetch-depth: 0`) on every push and pull request.
`git ls-files` shows no `.tfstate`, no `terraform.tfvars` and no `.env`; the two tracked
`dev.auto.tfvars` files carry **non-secret deployment configuration only** and say so in their first
line — flags such as `enable_msk` and `pricing_tier`, with the reasoning for each written above it.

---

## Known limitations

Each is stated with the control a real deployment would use instead.

### 1. The guardrail red-team score is a regression score, not a safety measurement

This is the most important sentence in this file, and it is already in the README. The 25-case
labelled set is scored against an **offline signature stand-in** for Bedrock's classifier, not
against Bedrock. Its 100% block rate proves that the *policy model* still refuses what it used to
refuse; it does not measure how the deployed classifier behaves.

What *is* proven about the deployed guardrail is its shape: attached, at an immutable version, every
policy class enabled, thresholds matching the policy model.

*A deployment would* run the red-team set against the live `ApplyGuardrail` API on a schedule and
track the block rate as an operational metric, accepting the cost and the non-determinism.

### 2. `secret_recovery_window_days = 0` in dev

Deleted secrets are purged immediately instead of parking in a recovery window. This is deliberate
and documented in `infra/aws/dev.auto.tfvars` with the incident that caused it: Secrets Manager
refuses to create a secret whose name is still scheduled for deletion, so with the 7-day default a
destroyed estate could not be rebuilt for a week — a build/destroy cycle that works exactly once.

The secrets in question are empty KMS-encrypted placeholders. **The setting is right here and wrong
in general**, and a production environment must not inherit it.

### 3. The deployer role is broad within its scope

The trust policy is tight (see above), but the role it grants still needs to create and destroy VPCs,
MSK clusters, IAM roles, KMS grants and Databricks workspaces. A compromise of a permitted workflow
is a compromise of the estate.

*A deployment would* split deploy from destroy, give each its own role, and require a second approver
on the destructive one.

### 4. The estate has no runtime threat detection

There is no GuardDuty, no Security Hub, no CloudTrail analysis, and no alerting on anomalous API
calls. Observability covers the *pipeline* — throughput, fraud-score distribution, verdict-gate
outcomes, guardrail blocks — not the account.

*A deployment would* enable GuardDuty and Security Hub at the organisation level and route findings
somewhere a human reads.

### 5. Drift is a library, not a monitor

`ml/monitoring/drift.py` computes PSI and two-sample KS with documented bands (0.10 / 0.25). No
scheduled job runs it, and nothing alerts. A model degrading in production would not be noticed by
this system.

### 6. The self-healing layer has never run live

`agents/langgraph/` implements bounded, idempotent remediation for known incident classes, covered by
37 tests with real LangGraph and mocked signals. It is not wired into the deploy, and an autonomous
remediation agent that has never faced a real incident is a liability rather than a control — which
is why it is not promoted anywhere in the README.

### 7. No SBOM and no container vulnerability scanning

`checkov` scans the Terraform, `gitleaks` scans for secrets, and dependencies are pinned — but
nothing produces an SBOM or scans the built streaming image for CVEs. Dependabot **security** updates
are enabled; routine version updates are deliberately off
([`.github/dependabot.yml`](.github/dependabot.yml)).

*A deployment would* add a Trivy or Grype scan to the image build and publish an SBOM per tag.

### 8. The local demo stack uses default credentials

`make e2e` brings up Grafana on `admin/admin` and publishes its ports on all interfaces. It is a
developer convenience on a single-user machine and should not be run on an untrusted network without
changing both.

---

## Pre-publish checklist

Before making the repository public, and after any change to the infrastructure or Responsible-AI
layers:

- [ ] `gitleaks` is green over the **full history**, not just the latest diff
- [ ] `git ls-files` lists no `.tfstate`, `terraform.tfvars`, `.env` or key material
- [ ] No real AWS account id, workspace URL or ARN is legible in a committed screenshot
- [ ] `make gate-proof` passes — every control still refuses its planted violation, for the right reason
- [ ] `make govern-docs --check` passes, so the generated regulated-AI documents match the code
- [ ] The OIDC trust policy still enumerates explicit branch and environment subjects — never `repo:*`
- [ ] `checkov` findings are either fixed or carry a scoped, commented `skip`
- [ ] The claimed metrics in the README badges still match a real run
