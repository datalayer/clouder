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

variable "enable_client_vpn" {
  description = "Enable AWS Client VPN endpoint for secure access to VPC resources."
  type        = bool
  default     = false
}

variable "client_vpn_client_cidr" {
  description = "CIDR assigned to VPN clients. Must not overlap with VPC CIDR."
  type        = string
  default     = "172.16.0.0/22"
}

variable "client_vpn_server_certificate_arn" {
  description = "ACM server certificate ARN for Client VPN endpoint."
  type        = string
  default     = null
}

variable "client_vpn_client_root_certificate_chain_arn" {
  description = "ACM client root certificate chain ARN for certificate-authenticated Client VPN."
  type        = string
  default     = null
}

variable "client_vpn_authorized_cidrs" {
  description = "CIDRs allowed and routed via Client VPN endpoint."
  type        = list(string)
  default     = []
}

variable "client_vpn_split_tunnel" {
  description = "Enable split tunnel for Client VPN endpoint."
  type        = bool
  default     = true
}

variable "client_vpn_transport_protocol" {
  description = "Transport protocol for Client VPN endpoint."
  type        = string
  default     = "udp"
}

variable "client_vpn_session_timeout_hours" {
  description = "Session timeout in hours for Client VPN."
  type        = number
  default     = 8
}

variable "client_vpn_dns_servers" {
  description = "Optional DNS servers pushed to VPN clients."
  type        = list(string)
  default     = []
}
