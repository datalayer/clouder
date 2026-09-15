output "registry" {
  value = module.environments_registry.registry
}

output "region" {
  value = module.environments_registry.region
}

output "repository_prefix" {
  value = module.environments_registry.repository_prefix
}

output "base_repositories" {
  value = module.environments_registry.base_repositories
}

output "encryption_key_arn" {
  value = module.environments_registry.encryption_key_arn
}

output "signing_key_arn" {
  value = module.environments_registry.signing_key_arn
}

output "signing_key_alias" {
  value = module.environments_registry.signing_key_alias
}

output "builder_user" {
  value = module.environments_registry.builder_user
}

output "puller_user" {
  value = module.environments_registry.puller_user
}

output "reader_user" {
  value = module.environments_registry.reader_user
}

output "base_reader_role_arn" {
  value = module.environments_registry.base_reader_role_arn
}
