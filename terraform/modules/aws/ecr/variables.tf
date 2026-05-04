variable "project_name" {
  description = "Project prefix used in tags."
  type        = string
}

variable "repository_names" {
  description = "List of ECR repository names to create."
  type        = list(string)
}

variable "scan_on_push" {
  description = "Enable image scan on push."
  type        = bool
  default     = true
}
