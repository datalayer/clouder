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

variable "aws_node_iam_managed_policy_arns" {
  description = "AWS managed policy ARNs attached to kubeadm node IAM role."
  type        = list(string)
  default = [
    "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy",
    "arn:aws:iam::aws:policy/ElasticLoadBalancingFullAccess",
    "arn:aws:iam::aws:policy/AmazonEC2ReadOnlyAccess",
    "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
  ]
}

variable "enable_client_vpn" {
  description = "Enable AWS Client VPN endpoint for secure operator access to kubeadm VPC resources."
  type        = bool
  default     = false
}

variable "client_vpn_client_cidr" {
  description = "CIDR assigned to VPN clients (must not overlap with VPC CIDR)."
  type        = string
  default     = "172.16.0.0/22"
}

variable "client_vpn_server_certificate_arn" {
  description = "ACM server certificate ARN for AWS Client VPN endpoint. Required when enable_client_vpn is true."
  type        = string
  default     = null

  validation {
    condition     = !var.enable_client_vpn || var.client_vpn_server_certificate_arn != null
    error_message = "client_vpn_server_certificate_arn is required when enable_client_vpn is true."
  }
}

variable "client_vpn_client_root_certificate_chain_arn" {
  description = "ACM client root certificate chain ARN for certificate-authenticated AWS Client VPN. Required when enable_client_vpn is true."
  type        = string
  default     = null

  validation {
    condition     = !var.enable_client_vpn || var.client_vpn_client_root_certificate_chain_arn != null
    error_message = "client_vpn_client_root_certificate_chain_arn is required when enable_client_vpn is true."
  }
}

variable "client_vpn_authorized_cidrs" {
  description = "CIDRs reachable through Client VPN authorization and route rules. If empty, defaults to vpc_cidr."
  type        = list(string)
  default     = []
}

variable "client_vpn_split_tunnel" {
  description = "Whether Client VPN should use split tunnel mode."
  type        = bool
  default     = true
}

variable "client_vpn_transport_protocol" {
  description = "Transport protocol for Client VPN endpoint."
  type        = string
  default     = "udp"

  validation {
    condition     = contains(["udp", "tcp"], var.client_vpn_transport_protocol)
    error_message = "client_vpn_transport_protocol must be either 'udp' or 'tcp'."
  }
}

variable "client_vpn_session_timeout_hours" {
  description = "Client VPN session timeout in hours."
  type        = number
  default     = 8

  validation {
    condition     = contains([8, 10, 12, 24], var.client_vpn_session_timeout_hours)
    error_message = "client_vpn_session_timeout_hours must be one of: 8, 10, 12, 24."
  }
}

variable "client_vpn_dns_servers" {
  description = "Optional DNS servers for Client VPN clients."
  type        = list(string)
  default     = []
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
