SHELL := /bin/bash

TF ?= terraform
TFVARS ?= terraform.tfvars
PLAN ?= tfplan
PLAN_JSON ?= tfplan.json
APPROVED ?= false

# Optional AWS auth/region context.
AWS_PROFILE ?=
AWS_REGION ?=
AWS_DEFAULT_REGION ?= $(AWS_REGION)

ifneq ($(strip $(AWS_PROFILE)),)
export AWS_PROFILE
endif
ifneq ($(strip $(AWS_REGION)),)
export AWS_REGION
endif
ifneq ($(strip $(AWS_DEFAULT_REGION)),)
export AWS_DEFAULT_REGION
endif

.DEFAULT_GOAL := help

.PHONY: help check-tools check-aws check-kubeadm-mode tfvars init fmt validate plan apply apply-auto output \
	ci-plan ci-apply bootstrap deploy-all deploy-rollout deploy-service full-deploy destroy clean

help: ## Display available targets
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z0-9_.-]+:.*##/ { printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

check-tools: ## Check required local binaries
	@command -v $(TF) >/dev/null || (echo "Missing terraform CLI" && exit 1)
	@command -v aws >/dev/null || (echo "Missing aws CLI" && exit 1)
	@command -v clouder >/dev/null || (echo "Missing clouder CLI" && exit 1)
	@command -v plane >/dev/null || (echo "Missing plane CLI" && exit 1)
	@echo "Tools check passed."

check-aws: ## Validate AWS caller identity with current credentials
	@aws sts get-caller-identity >/dev/null
	@echo "AWS credentials check passed."

check-kubeadm-mode: ## Ensure this Terraform stack is kubeadm-based (no EKS resources)
	@if grep -R --include='*.tf' -n 'resource[[:space:]]*"aws_eks_' . >/dev/null; then \
		echo "EKS resource found. This stack must remain kubeadm-based."; \
		exit 1; \
	fi
	@if ! grep -q 'clouder kubeadm setup' ./templates/aws/clouder-kubeadm-setup.sh.tpl; then \
		echo "Missing kubeadm setup command in templates/aws/clouder-kubeadm-setup.sh.tpl"; \
		exit 1; \
	fi
	@echo "Kubeadm mode check passed (no EKS resources, clouder kubeadm setup present)."

tfvars: ## Create terraform.tfvars from example if missing
	@test -f $(TFVARS) || cp terraform.tfvars.example $(TFVARS)
	@echo "Using $(TFVARS)"

init: check-tools ## Initialize Terraform working directory
	$(TF) init

fmt: check-tools ## Format Terraform files
	$(TF) fmt -recursive

validate: init ## Validate Terraform configuration
	$(TF) validate

plan: init tfvars check-aws check-kubeadm-mode ## Generate Terraform plan
	$(TF) plan -var-file=$(TFVARS) -out=$(PLAN)

apply: init tfvars check-aws check-kubeadm-mode ## Apply previously generated Terraform plan
	$(TF) apply $(PLAN)

apply-auto: init tfvars check-aws check-kubeadm-mode ## Plan and apply in one step
	$(TF) apply -var-file=$(TFVARS) -auto-approve

output: ## Show all Terraform outputs
	$(TF) output

ci-plan: init tfvars check-aws check-kubeadm-mode ## Non-interactive CI plan + JSON artifact
	$(TF) plan -input=false -lock-timeout=5m -var-file=$(TFVARS) -out=$(PLAN)
	$(TF) show -json $(PLAN) > $(PLAN_JSON)
	@echo "Created plan artifacts: $(PLAN) and $(PLAN_JSON)"

ci-apply: init tfvars check-aws check-kubeadm-mode ## Non-interactive gated apply (requires APPROVED=true)
	@test "$(APPROVED)" = "true" || (echo "Refusing apply: set APPROVED=true after CI approval gate." && exit 1)
	@test -f $(PLAN) || (echo "Missing $(PLAN). Run make ci-plan first." && exit 1)
	$(TF) apply -input=false $(PLAN)

bootstrap: ## Run generated Clouder Kubeadm setup script
	@test -x ./generated/clouder-kubeadm-setup.sh || (echo "Missing generated/clouder-kubeadm-setup.sh. Run make apply-auto first." && exit 1)
	./generated/clouder-kubeadm-setup.sh

deploy-all: ## Deploy all Datalayer services via generated helper
	@test -x ./generated/plane-deploy-all-services.sh || (echo "Missing generated/plane-deploy-all-services.sh. Run make apply-auto first." && exit 1)
	./generated/plane-deploy-all-services.sh

deploy-rollout: ## Deploy Datalayer services in staged rollout order
	@test -x ./generated/plane-deploy-rollout.sh || (echo "Missing generated/plane-deploy-rollout.sh. Run make apply-auto first." && exit 1)
	./generated/plane-deploy-rollout.sh

deploy-service: ## Deploy one service: make deploy-service SERVICE=datalayer-iam
	@test -n "$(SERVICE)" || (echo "Provide SERVICE=<service-name>" && exit 1)
	@test -x ./generated/services/deploy-$(SERVICE).sh || (echo "Missing generated/services/deploy-$(SERVICE).sh" && exit 1)
	./generated/services/deploy-$(SERVICE).sh

full-deploy: apply-auto bootstrap deploy-rollout ## End-to-end deployment workflow
	@echo "Full deployment completed."

destroy: init tfvars check-aws ## Destroy deployed infrastructure
	$(TF) destroy -var-file=$(TFVARS)

clean: ## Remove local plan artifacts
	rm -f $(PLAN) $(PLAN_JSON)
