#!/usr/bin/env bash
set -euo pipefail

cat <<'EOM'
This script deploys a single Datalayer service with Plane.
Prerequisites:
1. Plane installed and authenticated to your registries.
2. KUBECONFIG exported for ${cluster_name}.
3. Service env vars sourced from your datalayerrc.
EOM

plane up ${service_name}
plane ls
