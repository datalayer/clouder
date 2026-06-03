#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/kubeadm-cluster.env"

echo "Setting Clouder context to AWS account ${aws_account_id}"
clouder ctx set aws ${aws_account_id}

echo "Running kubeadm setup for cluster ${cluster_name}"
clouder kubeadm setup ${cluster_name} --admin-user ${admin_user} --key ${ssh_key_name}

echo "Fetching kubeconfig"
clouder kubeadm get-config ${cluster_name} --admin-user ${admin_user} --key ${ssh_key_name}

cat <<'EOM'

Setup complete.

Export kubeconfig:
  export KUBECONFIG=~/.clouder/kubeadm//kubeconfig

Validate:
  kubectl get nodes -o wide

EOM
