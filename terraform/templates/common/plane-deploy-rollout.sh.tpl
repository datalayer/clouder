#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

run_service() {
  local stage="$1"
  local service="$2"
  local script="$SCRIPT_DIR/services/deploy-${service}.sh"

  if [[ ! -f "$script" ]]; then
    echo "Missing deployment script: $script" >&2
    exit 1
  fi

  echo "[$stage] deploying $service"
  bash "$script"
}

cat <<'EOM'
This script runs an ordered Datalayer service rollout.
Stages: system -> core -> optional -> custom.
Each stage uses per-service scripts under generated/services.
EOM

%{ for service in system_services ~}
run_service "system" "${service}"
%{ endfor ~}

%{ for service in core_services ~}
run_service "core" "${service}"
%{ endfor ~}

%{ for service in optional_services ~}
run_service "optional" "${service}"
%{ endfor ~}

%{ for service in custom_services ~}
run_service "custom" "${service}"
%{ endfor ~}

echo "Rollout complete."
