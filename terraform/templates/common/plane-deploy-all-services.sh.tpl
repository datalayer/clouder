#!/usr/bin/env bash
set -euo pipefail

cat <<'EOM'
This script deploys the Datalayer service stack with Plane.
Prerequisites:
1. Plane installed and authenticated to your registries.
2. KUBECONFIG exported for ${cluster_name}.
3. Service env vars sourced from your datalayerrc.
EOM

# System services
plane up datalayer-cert-manager
plane up datalayer-traefik
plane up datalayer-solr-operator
plane up datalayer-otel
plane up datalayer-observer
plane up datalayer-vault
plane up datalayer-kafka
plane up datalayer-pulsar
plane up datalayer-openfga
plane up datalayer-datashim
plane up datalayer-mailer

# Core Datalayer services
plane up datalayer-operator
plane up datalayer-iam
plane up datalayer-runtimes
plane up datalayer-library
plane up datalayer-spacer
plane up datalayer-ai-agents
plane up datalayer-functions
plane up datalayer-scheduler
plane up datalayer-spider
plane up datalayer-manager
plane up datalayer-status

# Optional addons/services
plane up datalayer-shared-filesystem || true
plane up datalayer-storage-operator || true
plane up datalayer-storage-cluster || true

echo "All requested services submitted."
plane list
