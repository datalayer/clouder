output "vpc_id" {
  description = "Created VPC ID."
  value       = aws_vpc.this.id
}

output "public_subnet_id" {
  description = "Public subnet ID for kubeadm nodes."
  value       = aws_subnet.public.id
}

output "security_group_id" {
  description = "Security group attached to control plane and worker nodes."
  value       = aws_security_group.kubeadm.id
}

output "availability_zone" {
  description = "AZ where the subnet is created."
  value       = aws_subnet.public.availability_zone
}
