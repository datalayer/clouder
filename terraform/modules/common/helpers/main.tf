locals {
  env_template_path   = "${var.templates_dir}/${var.cloud_provider}/kubeadm-cluster.env.tpl"
  setup_template_path = "${var.templates_dir}/${var.cloud_provider}/clouder-kubeadm-setup.sh.tpl"
  deploy_template_path = "${var.templates_dir}/common/plane-deploy-all-services.sh.tpl"
  deploy_one_template_path = "${var.templates_dir}/common/plane-deploy-service.sh.tpl"
  deploy_rollout_template_path = "${var.templates_dir}/common/plane-deploy-rollout.sh.tpl"
  effective_service_deployments = length(var.service_deployments) > 0 ? var.service_deployments : concat(
    var.system_service_deployments,
    var.core_service_deployments,
    var.optional_service_deployments,
  )
  custom_service_deployments = [
    for service in local.effective_service_deployments : service
    if !contains(var.system_service_deployments, service)
    && !contains(var.core_service_deployments, service)
    && !contains(var.optional_service_deployments, service)
  ]
}

resource "local_file" "kubeadm_cluster_env" {
  count = var.generate_files ? 1 : 0

  filename = "${var.generated_dir}/kubeadm-cluster.env"
  content = templatefile(local.env_template_path, {
    cluster_name   = var.cluster_name
    aws_region     = var.cloud_region
    aws_account_id = var.cloud_account_id
    admin_user     = var.admin_user
    master_ip      = var.master_ip
    worker_ips_csv = var.worker_ips_csv
    ssh_key_name   = var.ssh_key_name
    ecr_registry   = var.registry
  })
}

resource "local_file" "clouder_kubeadm_setup" {
  count = var.generate_files ? 1 : 0

  filename        = "${var.generated_dir}/clouder-kubeadm-setup.sh"
  file_permission = "0755"
  content = templatefile(local.setup_template_path, {
    cluster_name   = var.cluster_name
    aws_account_id = var.cloud_account_id
    admin_user     = var.admin_user
    ssh_key_name   = var.ssh_key_name
  })
}

resource "local_file" "plane_deploy_all_services" {
  count = var.generate_files ? 1 : 0

  filename        = "${var.generated_dir}/plane-deploy-all-services.sh"
  file_permission = "0755"
  content = templatefile(local.deploy_template_path, {
    cluster_name = var.cluster_name
  })
}

resource "local_file" "plane_deploy_rollout" {
  count = var.generate_files ? 1 : 0

  filename        = "${var.generated_dir}/plane-deploy-rollout.sh"
  file_permission = "0755"
  content = templatefile(local.deploy_rollout_template_path, {
    cluster_name = var.cluster_name
    system_services = [for service in var.system_service_deployments : service if contains(toset(local.effective_service_deployments), service)]
    core_services = [for service in var.core_service_deployments : service if contains(toset(local.effective_service_deployments), service)]
    optional_services = [for service in var.optional_service_deployments : service if contains(toset(local.effective_service_deployments), service)]
    custom_services = local.custom_service_deployments
  })
}

resource "local_file" "plane_deploy_service" {
  for_each = var.generate_files ? toset(local.effective_service_deployments) : toset([])

  filename        = "${var.generated_dir}/services/deploy-${each.value}.sh"
  file_permission = "0755"
  content = templatefile(local.deploy_one_template_path, {
    cluster_name = var.cluster_name
    service_name = each.value
  })
}
