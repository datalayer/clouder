SHELL=/bin/bash

CONDA=source $$(conda info --base)/etc/profile.d/conda.sh
CONDA_ACTIVATE=source $$(conda info --base)/etc/profile.d/conda.sh ; conda activate
CONDA_DEACTIVATE=source $$(conda info --base)/etc/profile.d/conda.sh ; conda deactivate
CONDA_REMOVE=source $$(conda info --base)/etc/profile.d/conda.sh ; conda remove -y --all -n

ENV_NAME=datalayer

.DEFAULT_GOAL := default

.PHONY: init apply output destro all

help: ## display this help.
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

default: help ## Default target is all.

init: # terraform init
	($(CONDA_ACTIVATE) ${ENV_NAME}; \
		python ./py/init.py )

apply: # terraform apply
	($(CONDA_ACTIVATE) ${ENV_NAME}; \
		python ./py/apply.py )

output: # terraform output
	($(CONDA_ACTIVATE) ${ENV_NAME}; \
		python ./py/output.py )

destroy: # terraform destroy
	($(CONDA_ACTIVATE) ${ENV_NAME}; \
		python ./py/destroy.py )
