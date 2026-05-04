variable "cluster_name" {
  description = "Cluster name used in helper scripts."
  type        = string
}

variable "cloud_provider" {
  description = "Cloud provider key used to resolve provider templates (e.g. aws, azure)."
  type        = string
}

variable "cloud_region" {
  description = "Cloud region used in generated environment file."
  type        = string
}

variable "cloud_account_id" {
  description = "Cloud account identifier used by provider-specific setup scripts."
  type        = string
}

variable "admin_user" {
  description = "Admin username used by cluster setup commands."
  type        = string
}

variable "master_ip" {
  description = "Master node public IP."
  type        = string
}

variable "worker_ips_csv" {
  description = "Worker node public IPs as comma-separated values."
  type        = string
}

variable "ssh_key_name" {
  description = "SSH key name consumed by Clouder setup script."
  type        = string
}

variable "registry" {
  description = "Container registry endpoint used by deployment scripts."
  type        = string
}

variable "templates_dir" {
  description = "Absolute path to terraform templates root."
  type        = string
}

variable "generated_dir" {
  description = "Absolute path to generated helper files output directory."
  type        = string
}

variable "generate_files" {
  description = "Whether to generate helper files."
  type        = bool
  default     = true
}

variable "system_service_deployments" {
  description = "System services deployed before core APIs."
  type        = list(string)
  default = [
    "datalayer-cert-manager",
    "datalayer-traefik",
    "datalayer-solr-operator",
    "datalayer-otel",
    "datalayer-observer",
    "datalayer-vault",
    "datalayer-kafka",
    "datalayer-pulsar",
    "datalayer-openfga",
    "datalayer-datashim",
    "datalayer-mailer",
    "datalayer-home",
    "datalayer-cuda-operator",
    "datalayer-nginx",
  ]
}

variable "core_service_deployments" {
  description = "Core Datalayer API and control-plane services."
  type        = list(string)
  default = [
    "datalayer-operator",
    "datalayer-iam",
    "datalayer-runtimes",
    "datalayer-library",
    "datalayer-spacer",
    "datalayer-ai-agents",
    "datalayer-functions",
    "datalayer-scheduler",
    "datalayer-spider",
    "datalayer-manager",
    "datalayer-status",
  ]
}

variable "optional_service_deployments" {
  description = "Optional services usually enabled after core stack is healthy."
  type        = list(string)
  default = [
    "datalayer-shared-filesystem",
    "datalayer-storage-operator",
    "datalayer-storage-cluster",
  ]
}

variable "service_deployments" {
  description = "Optional override list for per-service script generation. If empty, grouped lists are used."
  type        = list(string)
  default     = []
}
