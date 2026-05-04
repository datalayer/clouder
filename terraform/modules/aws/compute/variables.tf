variable "project_name" {
  description = "Project prefix used for AWS resource naming and tags."
  type        = string
}

variable "cluster_name" {
  description = "Cluster name used in EC2 instance naming."
  type        = string
}

variable "subnet_id" {
  description = "Subnet ID where all kubeadm nodes are created."
  type        = string
}

variable "security_group_id" {
  description = "Security group ID attached to all nodes."
  type        = string
}

variable "ssh_key_name" {
  description = "Name of an existing EC2 key pair to attach to instances."
  type        = string
}

variable "master_count" {
  description = "Number of control plane nodes. Keep at 1 for this first AWS blueprint."
  type        = number
  default     = 1
}

variable "worker_count" {
  description = "Number of worker nodes."
  type        = number
  default     = 3
}

variable "master_instance_type" {
  description = "EC2 instance type for control plane nodes."
  type        = string
}

variable "worker_instance_type" {
  description = "EC2 instance type for worker nodes."
  type        = string
}

variable "root_volume_size_gb" {
  description = "Root EBS volume size (GB) for each node."
  type        = number
}

variable "admin_user" {
  description = "Default SSH username on the AMI."
  type        = string
  default     = "ubuntu"
}

variable "ami_id" {
  description = "AMI ID override. If null, latest Ubuntu 22.04 LTS AMI is used."
  type        = string
  default     = null
}

variable "instance_profile_name" {
  description = "Optional IAM instance profile name to attach to all nodes."
  type        = string
  default     = null
}
