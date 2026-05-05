output "aws_account_id" {
  description = "AWS account id used for this deployment."
  value       = data.aws_caller_identity.current.account_id
}

output "cluster_name" {
  description = "kubeadm cluster name."
  value       = var.cluster_name
}

output "aws_region" {
  description = "AWS region used for deployment."
  value       = var.aws_region
}

output "kubeadm_master_public_ip" {
  description = "Public IP of kubeadm control plane node."
  value       = try(module.aws_compute.master_public_ips[0], null)
}

output "kubeadm_worker_public_ips" {
  description = "Public IPs of kubeadm worker nodes."
  value       = module.aws_compute.worker_public_ips
}

output "network" {
  description = "Created network IDs."
  value = {
    vpc_id                         = module.aws_network.vpc_id
    internet_gateway_id            = module.aws_network.internet_gateway_id
    public_subnet_id               = module.aws_network.public_subnet_id
    public_route_table_id          = module.aws_network.public_route_table_id
    security_group_id              = module.aws_network.security_group_id
    kubeadm_access_cidrs           = module.aws_network.kubeadm_access_cidrs
    client_vpn_enabled             = module.aws_network.client_vpn_enabled
    client_vpn_endpoint_id         = module.aws_network.client_vpn_endpoint_id
    client_vpn_endpoint_dns_name   = module.aws_network.client_vpn_endpoint_dns_name
    client_vpn_authorized_cidrs    = module.aws_network.client_vpn_authorized_cidrs
  }
}

output "ecr_registry" {
  description = "Base ECR registry endpoint."
  value       = "${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.aws_region}.amazonaws.com"
}

output "ecr_repositories" {
  description = "Map of created ECR repositories."
  value       = module.aws_ecr.repositories
}

output "kubeadm_nodes_iam_role" {
  description = "IAM role attached to kubeadm EC2 nodes for CSI and storage operations."
  value       = module.aws_iam.role_arn
}

output "kubeadm_nodes_iam_role_name" {
  description = "IAM role name attached to kubeadm EC2 nodes."
  value       = module.aws_iam.role_name
}

output "kubeadm_nodes_instance_profile" {
  description = "IAM instance profile attached to kubeadm EC2 nodes."
  value       = module.aws_iam.instance_profile_arn
}

output "kubeadm_nodes_instance_profile_name" {
  description = "IAM instance profile name attached to kubeadm EC2 nodes."
  value       = module.aws_iam.instance_profile_name
}

output "kubeadm_nodes_iam_policies" {
  description = "Managed policies attached to kubeadm nodes IAM role."
  value       = module.aws_iam.attached_policy_arns
}

output "generated_files" {
  description = "Paths to generated helper files after terraform apply."
  value       = module.common_helpers.generated_files
}

output "generated_service_files" {
  description = "Map of generated per-service deploy scripts keyed by service name."
  value       = module.common_helpers.generated_service_files
}

output "generated_rollout_script" {
  description = "Path to generated rollout script for staged service deployment."
  value       = module.common_helpers.generated_rollout_script
}

output "generated_service_files_by_group" {
  description = "Per-service deploy scripts grouped by system/core/optional deployment stages."
  value       = module.common_helpers.generated_service_files_by_group
}

output "generated_service_rollout_sequence" {
  description = "Ordered rollout sequence with stage, service, and script fields."
  value       = module.common_helpers.generated_service_rollout_sequence
}

output "generated_service_rollout_scripts" {
  description = "Ordered per-service deploy script paths for direct pipeline execution."
  value       = module.common_helpers.generated_service_rollout_scripts
}
