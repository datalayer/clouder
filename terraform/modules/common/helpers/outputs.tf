locals {
  effective_service_deployments = length(var.service_deployments) > 0 ? var.service_deployments : concat(
    var.system_service_deployments,
    var.core_service_deployments,
    var.optional_service_deployments,
  )
  effective_service_set = toset(local.effective_service_deployments)
  custom_service_deployments = [
    for service in local.effective_service_deployments : service
    if !contains(var.system_service_deployments, service)
    && !contains(var.core_service_deployments, service)
    && !contains(var.optional_service_deployments, service)
  ]
  rollout_sequence = concat(
    [for service in var.system_service_deployments : {
      stage   = "system"
      service = service
      script  = "${var.generated_dir}/services/deploy-${service}.sh"
    } if contains(local.effective_service_set, service)],
    [for service in var.core_service_deployments : {
      stage   = "core"
      service = service
      script  = "${var.generated_dir}/services/deploy-${service}.sh"
    } if contains(local.effective_service_set, service)],
    [for service in var.optional_service_deployments : {
      stage   = "optional"
      service = service
      script  = "${var.generated_dir}/services/deploy-${service}.sh"
    } if contains(local.effective_service_set, service)],
    [for service in local.custom_service_deployments : {
      stage   = "custom"
      service = service
      script  = "${var.generated_dir}/services/deploy-${service}.sh"
    }]
  )
}

output "generated_files" {
  description = "Paths to generated helper files."
  value = var.generate_files ? concat([
    "${var.generated_dir}/kubeadm-cluster.env",
    "${var.generated_dir}/clouder-kubeadm-setup.sh",
    "${var.generated_dir}/plane-deploy-all-services.sh",
    "${var.generated_dir}/plane-deploy-rollout.sh",
    ], [for service in local.effective_service_deployments : "${var.generated_dir}/services/deploy-${service}.sh"]
  ) : []
}

output "generated_rollout_script" {
  description = "Path to generated rollout script that executes per-service scripts in stage order."
  value       = var.generate_files ? "${var.generated_dir}/plane-deploy-rollout.sh" : null
}

output "generated_service_files" {
  description = "Map of generated per-service deploy helper scripts keyed by service name."
  value = var.generate_files ? {
    for service in local.effective_service_deployments :
    service => "${var.generated_dir}/services/deploy-${service}.sh"
  } : {}
}

output "generated_service_files_by_group" {
  description = "Per-service deploy helper scripts grouped by deployment stage."
  value = var.generate_files ? {
    system = {
      for service in var.system_service_deployments :
      service => "${var.generated_dir}/services/deploy-${service}.sh"
      if contains(local.effective_service_set, service)
    }
    core = {
      for service in var.core_service_deployments :
      service => "${var.generated_dir}/services/deploy-${service}.sh"
      if contains(local.effective_service_set, service)
    }
    optional = {
      for service in var.optional_service_deployments :
      service => "${var.generated_dir}/services/deploy-${service}.sh"
      if contains(local.effective_service_set, service)
    }
  } : {
    system   = {}
    core     = {}
    optional = {}
  }
}

output "generated_service_rollout_sequence" {
  description = "Ordered rollout sequence for per-service deploy scripts with stage metadata."
  value = var.generate_files ? local.rollout_sequence : []
}

output "generated_service_rollout_scripts" {
  description = "Ordered per-service deploy script paths for pipeline execution."
  value = var.generate_files ? [for entry in local.rollout_sequence : entry.script] : []
}
