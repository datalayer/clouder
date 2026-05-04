variable "project_name" {
  description = "Project prefix used for AWS resource naming and tags."
  type        = string
}

variable "cluster_name" {
  description = "Logical cluster name used in tags and helper script outputs."
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
}

variable "subnet_cidr" {
  description = "CIDR block for the public subnet used by kubeadm nodes."
  type        = string
}

variable "availability_zone" {
  description = "Specific AZ to place the subnet in. If null, the first available AZ is used."
  type        = string
  default     = null
}

variable "allowed_ssh_cidrs" {
  description = "CIDR blocks allowed to SSH to cluster nodes."
  type        = list(string)
}
