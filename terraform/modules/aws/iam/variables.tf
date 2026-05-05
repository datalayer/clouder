variable "project_name" {
  description = "Project prefix used for IAM resource names."
  type        = string
}

variable "cluster_name" {
  description = "Cluster name used for IAM resource names."
  type        = string
}

variable "node_managed_policy_arns" {
  description = "Managed policy ARNs attached to kubeadm nodes IAM role."
  type        = list(string)
}