variable "project_name" {
  description = "Project prefix used in resource names and tags."
  type        = string
  default     = "datalayer"
}

variable "repository_prefix" {
  description = "The ECR repository prefix every Environment artifact lives under. IAM policies are scoped to it."
  type        = string
  default     = "environments"
}

variable "base_channels" {
  description = "The base channel repositories to create under <prefix>/base/, one per base (python-cpu, python-cuda)."
  type        = list(string)
  default     = ["python-cpu", "python-cuda"]
}

variable "manage_registry_scanning" {
  description = <<-EOT
    Set the account's ECR registry scanning to ENHANCED (Amazon Inspector) for the
    Environment repositories. The registry scanning configuration is one per account and
    region: when true, this module owns it, so add any other repository filter that must
    keep being scanned to extra_scan_filters.
  EOT
  type        = bool
  default     = true
}

variable "scan_frequency" {
  description = "Enhanced scanning frequency for the Environment repositories: SCAN_ON_PUSH or CONTINUOUS_SCAN."
  type        = string
  default     = "CONTINUOUS_SCAN"

  validation {
    condition     = contains(["SCAN_ON_PUSH", "CONTINUOUS_SCAN"], var.scan_frequency)
    error_message = "scan_frequency must be SCAN_ON_PUSH or CONTINUOUS_SCAN."
  }
}

variable "extra_scan_filters" {
  description = "Other repository filters (wildcards) enhanced scanning keeps covering when this module owns the configuration."
  type        = list(string)
  default     = []
}

variable "base_reader_session_seconds" {
  description = "The longest session the base-reader role grants, for one managed-provider build."
  type        = number
  default     = 3600
}

variable "kms_deletion_window_in_days" {
  description = "How long a deleted KMS key can still be recovered."
  type        = number
  default     = 30
}

variable "tags" {
  description = "Tags added to every resource."
  type        = map(string)
  default     = {}
}
