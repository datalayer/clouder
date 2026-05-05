output "role_name" {
  description = "IAM role name attached to kubeadm EC2 nodes."
  value       = aws_iam_role.kubeadm_nodes.name
}

output "role_arn" {
  description = "IAM role ARN attached to kubeadm EC2 nodes."
  value       = aws_iam_role.kubeadm_nodes.arn
}

output "instance_profile_name" {
  description = "Instance profile name attached to kubeadm EC2 nodes."
  value       = aws_iam_instance_profile.kubeadm_nodes.name
}

output "instance_profile_arn" {
  description = "Instance profile ARN attached to kubeadm EC2 nodes."
  value       = aws_iam_instance_profile.kubeadm_nodes.arn
}

output "attached_policy_arns" {
  description = "Managed policy ARNs attached to kubeadm EC2 role."
  value       = sort([for attachment in aws_iam_role_policy_attachment.node_managed_policies : attachment.policy_arn])
}