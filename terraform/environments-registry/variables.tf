variable "aws_region" {
  description = "The region of the Environments registry: us-east-1 (PLAN_ENV.md, D-16)."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project prefix used in resource names and tags."
  type        = string
  default     = "datalayer"
}

variable "repository_prefix" {
  description = "The ECR repository prefix every Environment artifact lives under."
  type        = string
  default     = "environments"
}

variable "base_channels" {
  description = "The base channel repositories to create."
  type        = list(string)
  default     = ["python-cpu", "python-cuda"]
}

variable "manage_registry_scanning" {
  description = "Own the account's registry scanning configuration, setting it to ENHANCED for the Environment repositories."
  type        = bool
  default     = true
}

variable "scan_frequency" {
  description = "SCAN_ON_PUSH or CONTINUOUS_SCAN."
  type        = string
  default     = "CONTINUOUS_SCAN"
}

variable "extra_scan_filters" {
  description = "Other repository wildcards enhanced scanning must keep covering."
  type        = list(string)
  default     = []
}

variable "base_reader_session_seconds" {
  description = "The longest session a managed-provider build is handed."
  type        = number
  default     = 3600
}

variable "tags" {
  description = "Tags added to every resource."
  type        = map(string)
  default     = {}
}

variable "kms_deletion_window_in_days" {
  description = "How long a deleted KMS key can still be recovered: 30 days for the real registry, 7 for a scratch one."
  type        = number
  default     = 30

  validation {
    condition     = var.kms_deletion_window_in_days >= 7 && var.kms_deletion_window_in_days <= 30
    error_message = "AWS KMS allows a deletion window of 7 to 30 days."
  }
}
