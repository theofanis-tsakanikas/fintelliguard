# FintelliGuard — developer entrypoints.
# Python tooling runs from the local .venv if present, else the PATH binary.

VENV    ?= .venv
RUFF    := $(if $(wildcard $(VENV)/bin/ruff),$(VENV)/bin/ruff,ruff)
PYTEST  := $(if $(wildcard $(VENV)/bin/pytest),$(VENV)/bin/pytest,pytest)

# Terraform layer to target for plan/apply, e.g. `make plan TF_DIR=infra/aws/bootstrap`
TF_DIR  ?= infra/aws/bootstrap

.DEFAULT_GOAL := help
.PHONY: help fmt lint test plan apply

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

# --- Terraform (stubs — wired up as IaC layers land) -------------------------
# Usage: make plan  TF_DIR=infra/aws
#        make apply TF_DIR=infra/databricks

plan: ## terraform plan for $(TF_DIR)
	cd $(TF_DIR) && terraform init -input=false && terraform plan

apply: ## terraform apply for $(TF_DIR) (review the plan first!)
	cd $(TF_DIR) && terraform init -input=false && terraform apply
