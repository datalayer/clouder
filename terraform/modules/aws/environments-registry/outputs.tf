output "registry" {
  description = "The ECR registry host Environment images are pushed to."
  value       = local.registry
}

output "region" {
  description = "The region of the registry."
  value       = local.region
}

output "repository_prefix" {
  description = "The prefix every Environment repository lives under."
  value       = var.repository_prefix
}

output "base_repositories" {
  description = "Base channel repository URLs, by channel."
  value       = { for channel, repository in aws_ecr_repository.base : channel => repository.repository_url }
}

output "encryption_key_arn" {
  description = "The KMS key Environment images are encrypted with."
  value       = aws_kms_key.encryption.arn
}

output "signing_key_arn" {
  description = "The asymmetric KMS key Environment images are signed with."
  value       = aws_kms_key.signing.arn
}

output "signing_key_alias" {
  description = "The alias cosign names the signing key by: awskms:///<alias>."
  value       = aws_kms_alias.signing.name
}

output "builder_user" {
  description = "The IAM user the durable environments worker builds, pushes, signs and deletes as."
  value       = aws_iam_user.builder.name
}

output "puller_user" {
  description = "The IAM user runtime planes pull and verify as."
  value       = aws_iam_user.puller.name
}

output "reader_user" {
  description = "The IAM user Runtimes reads images and scan findings as."
  value       = aws_iam_user.reader.name
}

output "base_reader_role_arn" {
  description = "The role a managed-provider build is handed a session of, read-only on the bases."
  value       = aws_iam_role.base_reader.arn
}
