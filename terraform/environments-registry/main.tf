# The Environments registry root (PLAN_ENV.md, E0-12).
#
# Its own root and its own state, apart from the kubeadm stack in terraform/, which
# builds a cluster on AWS that the runtimes planes do not use. Apply it with
# `clouder aws ecr-environments apply` or `make apply` from this directory.

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

module "environments_registry" {
  source = "../modules/aws/environments-registry"

  project_name                = var.project_name
  repository_prefix           = var.repository_prefix
  base_channels               = var.base_channels
  manage_registry_scanning    = var.manage_registry_scanning
  scan_frequency              = var.scan_frequency
  extra_scan_filters          = var.extra_scan_filters
  base_reader_session_seconds = var.base_reader_session_seconds
  tags                        = var.tags
}
