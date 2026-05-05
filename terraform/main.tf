locals {
  ssh_key_name_for_clouder = var.ec2_key_pair_name

  ecr_registry = "${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.aws_region}.amazonaws.com"

  master_ip = try(module.aws_compute.master_public_ips[0], "")

  worker_ips_csv = join(",", module.aws_compute.worker_public_ips)
}

module "aws_network" {
  source = "./modules/aws/network"

  project_name                                  = var.project_name
  cluster_name                                  = var.cluster_name
  vpc_cidr                                      = var.vpc_cidr
  subnet_cidr                                   = var.subnet_cidr
  availability_zone                             = var.availability_zone
  allowed_ssh_cidrs                             = var.allowed_ssh_cidrs
  enable_client_vpn                             = var.enable_client_vpn
  client_vpn_client_cidr                        = var.client_vpn_client_cidr
  client_vpn_server_certificate_arn             = var.client_vpn_server_certificate_arn
  client_vpn_client_root_certificate_chain_arn  = var.client_vpn_client_root_certificate_chain_arn
  client_vpn_authorized_cidrs                   = var.client_vpn_authorized_cidrs
  client_vpn_split_tunnel                       = var.client_vpn_split_tunnel
  client_vpn_transport_protocol                 = var.client_vpn_transport_protocol
  client_vpn_session_timeout_hours              = var.client_vpn_session_timeout_hours
  client_vpn_dns_servers                        = var.client_vpn_dns_servers
}

module "aws_iam" {
  source = "./modules/aws/iam"

  project_name              = var.project_name
  cluster_name              = var.cluster_name
  node_managed_policy_arns  = var.aws_node_iam_managed_policy_arns
}

module "aws_compute" {
  source = "./modules/aws/compute"

  project_name          = var.project_name
  cluster_name          = var.cluster_name
  subnet_id             = module.aws_network.public_subnet_id
  security_group_id     = module.aws_network.security_group_id
  ssh_key_name          = var.ec2_key_pair_name
  master_count          = var.master_count
  worker_count          = var.worker_count
  master_instance_type  = var.master_instance_type
  worker_instance_type  = var.worker_instance_type
  root_volume_size_gb   = var.root_volume_size_gb
  admin_user            = var.admin_user
  ami_id                = var.ami_id
  instance_profile_name = module.aws_iam.instance_profile_name
}

module "aws_ecr" {
  source = "./modules/aws/ecr"

  project_name      = var.project_name
  repository_names  = var.datalayer_ecr_repositories
  scan_on_push      = true
}

module "common_helpers" {
  source = "./modules/common/helpers"

  cluster_name     = var.cluster_name
  cloud_provider   = "aws"
  cloud_region     = var.aws_region
  cloud_account_id = data.aws_caller_identity.current.account_id
  admin_user       = var.admin_user
  master_ip        = local.master_ip
  worker_ips_csv   = local.worker_ips_csv
  ssh_key_name     = local.ssh_key_name_for_clouder
  registry         = local.ecr_registry
  templates_dir    = "${path.module}/templates"
  generated_dir    = "${path.module}/generated"
  generate_files   = var.generate_helper_files
}
