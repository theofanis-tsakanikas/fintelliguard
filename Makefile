# FintelliGuard — developer entrypoints.
# Python tooling runs from the local .venv if present, else the PATH binary.

VENV    ?= .venv
RUFF    := $(if $(wildcard $(VENV)/bin/ruff),$(VENV)/bin/ruff,ruff)
PYTEST  := $(if $(wildcard $(VENV)/bin/pytest),$(VENV)/bin/pytest,pytest)

# Terraform layer to target for plan/apply, e.g. `make plan TF_DIR=infra/aws/bootstrap`
TF_DIR  ?= infra/aws/bootstrap

PY      := $(if $(wildcard $(VENV)/bin/python),$(VENV)/bin/python,python)

.DEFAULT_GOAL := help
.PHONY: help fmt lint test guardrail-scan gate-proof iac-scan govern-docs e2e e2e-down plan apply

COMPOSE := docker compose -f deploy/local/docker-compose.yml

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-8s\033[0m %s\n", $$1, $$2}'

fmt: ## Format Python with ruff
	$(RUFF) format .

lint: ## Lint Python with ruff (format check + rules)
	$(RUFF) check .
	$(RUFF) format --check .

test: ## Run the test suite
	$(PYTEST)

guardrail-scan: ## Run the guardrail red-team coverage gate
	$(PY) -m agents.bedrock.guardrails.evaluate

gate-proof: ## Attack our own gates: plant real violations, prove each gate refuses them
	$(PY) -m scripts.gate_proof

# NOT `$(PY) -m checkov`: checkov depends on `bc-python-hcl2`, an old fork that installs
# over the SAME `hcl2` package directory this repo's Terraform tests import. Putting it in
# the project venv silently changes how `agent.tf` parses and turns the guardrail
# attachment tests red — a dependency shadowing another with no warning. It runs isolated,
# and in CI it runs in its own container via the checkov action.
iac-scan: ## Security-scan the Terraform layers (checkov, isolated); skips documented in .checkov.yml
	@command -v uvx >/dev/null 2>&1 && uvx checkov --config-file .checkov.yml \
		|| pipx run checkov --config-file .checkov.yml

govern-docs: ## Regenerate the model/dataset cards + AI-Act technical docs
	$(PY) -m ml.governance.generate

e2e: ## One-command LOCAL end-to-end funnel (simulator -> Kafka -> scorer -> Prometheus -> Grafana). Grafana: :3000
	$(COMPOSE) up --build

e2e-down: ## Stop the local end-to-end funnel and remove its volumes
	$(COMPOSE) down -v

# --- Terraform (stubs — wired up as IaC layers land) -------------------------
# Usage: make plan  TF_DIR=infra/aws
#        make apply TF_DIR=infra/databricks

plan: ## terraform plan for $(TF_DIR)
	cd $(TF_DIR) && terraform init -input=false && terraform plan

apply: ## terraform apply for $(TF_DIR) (review the plan first!)
	cd $(TF_DIR) && terraform init -input=false && terraform apply
