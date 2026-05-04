variable "project_name" {
  description = "Project prefix for resource naming."
  type        = string
  default     = "datalayer"
}

variable "cluster_name" {
  description = "kubeadm cluster name used by Clouder and host naming."
  type        = string
  default     = "datalayer-aws"
}

variable "aws_region" {
  description = "AWS region for infrastructure deployment."
  type        = string
  default     = "us-east-1"
}

variable "availability_zone" {
  description = "Optional AZ override for the kubeadm subnet."
  type        = string
  default     = null
}

variable "vpc_cidr" {
  description = "CIDR block for VPC."
  type        = string
  default     = "10.0.0.0/16"
}

variable "subnet_cidr" {
  description = "CIDR block for kubeadm subnet."
  type        = string
  default     = "10.0.0.0/24"
}

variable "allowed_ssh_cidrs" {
  description = "CIDRs allowed for SSH and Kubernetes API access."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "ec2_key_pair_name" {
  description = "Existing AWS EC2 key pair name used for all nodes."
  type        = string
}

variable "master_count" {
  description = "Number of control plane nodes."
  type        = number
  default     = 1
}

variable "worker_count" {
  description = "Number of worker nodes."
  type        = number
  default     = 3
}

variable "master_instance_type" {
  description = "EC2 type for control plane node(s)."
  type        = string
  default     = "t3.large"
}

variable "worker_instance_type" {
  description = "EC2 type for worker nodes."
  type        = string
  default     = "t3.large"
}

variable "root_volume_size_gb" {
  description = "Root EBS volume size in GB for all nodes."
  type        = number
  default     = 100
}

variable "admin_user" {
  description = "SSH username for the chosen AMI."
  type        = string
  default     = "ubuntu"
}

variable "ami_id" {
  description = "Optional AMI override for all nodes."
  type        = string
  default     = null
}

variable "datalayer_ecr_repositories" {
  description = "ECR repositories to create for Datalayer service images."
  type        = list(string)
  default = [
    "datalayer/iam",
    "datalayer/runtimes",
    "datalayer/operator",
    "datalayer/library",
    "datalayer/spacer",
    "datalayer/ai-agents",
    "datalayer/functions",
    "datalayer/manager",
    "datalayer/status",
    "datalayer/mailer",
    "datalayer/scheduler",
    "datalayer/spider",
    "datalayer/success",
    "datalayer/support"
  ]
}

variable "generate_helper_files" {
  description = "Generate helper scripts and inventory files after apply."
  type        = bool
  default     = true
}
