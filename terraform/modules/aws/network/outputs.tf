output "vpc_id" {
  description = "Created VPC ID."
  value       = aws_vpc.this.id
}

output "internet_gateway_id" {
  description = "Internet gateway ID attached to VPC."
  value       = aws_internet_gateway.this.id
}

output "public_subnet_id" {
  description = "Public subnet ID for kubeadm nodes."
  value       = aws_subnet.public.id
}

output "public_route_table_id" {
  description = "Route table ID associated with public subnet."
  value       = aws_route_table.public.id
}

output "security_group_id" {
  description = "Security group attached to control plane and worker nodes."
  value       = aws_security_group.kubeadm.id
}

output "kubeadm_access_cidrs" {
  description = "CIDRs permitted for direct SSH and Kubernetes API access."
  value       = var.allowed_ssh_cidrs
}

output "availability_zone" {
  description = "AZ where the subnet is created."
  value       = aws_subnet.public.availability_zone
}

output "client_vpn_enabled" {
  description = "Whether Client VPN endpoint is enabled."
  value       = var.enable_client_vpn
}

output "client_vpn_endpoint_id" {
  description = "Client VPN endpoint ID when enabled."
  value       = var.enable_client_vpn ? aws_ec2_client_vpn_endpoint.this[0].id : null
}

output "client_vpn_endpoint_dns_name" {
  description = "Client VPN endpoint DNS name when enabled."
  value       = var.enable_client_vpn ? aws_ec2_client_vpn_endpoint.this[0].dns_name : null
}

output "client_vpn_authorized_cidrs" {
  description = "CIDRs authorized and routed through Client VPN."
  value       = var.enable_client_vpn ? (length(var.client_vpn_authorized_cidrs) > 0 ? var.client_vpn_authorized_cidrs : [var.vpc_cidr]) : []
}
