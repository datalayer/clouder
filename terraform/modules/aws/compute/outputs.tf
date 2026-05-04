output "master_public_ips" {
  description = "Public IPs of control plane nodes."
  value       = aws_instance.master[*].public_ip
}

output "master_private_ips" {
  description = "Private IPs of control plane nodes."
  value       = aws_instance.master[*].private_ip
}

output "worker_public_ips" {
  description = "Public IPs of worker nodes."
  value       = aws_instance.worker[*].public_ip
}

output "worker_private_ips" {
  description = "Private IPs of worker nodes."
  value       = aws_instance.worker[*].private_ip
}

output "master_instance_ids" {
  description = "Control plane instance IDs."
  value       = aws_instance.master[*].id
}

output "worker_instance_ids" {
  description = "Worker instance IDs."
  value       = aws_instance.worker[*].id
}
