variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for resources"
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "GCP zone for zonal resources"
  type        = string
  default     = "us-central1-a"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Environment must be one of: dev, staging, prod."
  }
}

variable "cluster_name" {
  description = "Name of the GKE cluster"
  type        = string
  default     = "neurafleet"
}

variable "node_count" {
  description = "Maximum number of nodes in the default node pool"
  type        = number
  default     = 3
}

variable "gpu_node_count" {
  description = "Maximum number of GPU nodes. 0 (the default) creates no GPU node pool: nothing in the platform uses a GPU today."
  type        = number
  default     = 0
}

variable "authorized_cidrs" {
  description = <<-EOT
    Source ranges allowed to reach the GKE control plane (master authorized networks),
    e.g. your office/VPN egress and CI runners.  Required: there is deliberately no default,
    and 0.0.0.0/0 is rejected.  Set it per environment in an untracked
    environments/<env>.local.tfvars and pass it as a second var-file:
      terraform apply -var-file=environments/prod.tfvars -var-file=environments/prod.local.tfvars
    where that file contains, for example:
      authorized_cidrs = [{ cidr_block = "203.0.113.7/32", display_name = "office" }]
  EOT
  type = list(object({
    cidr_block   = string
    display_name = string
  }))

  validation {
    condition     = length(var.authorized_cidrs) > 0
    error_message = "authorized_cidrs must contain at least one range."
  }

  validation {
    condition     = !contains([for c in var.authorized_cidrs : c.cidr_block], "0.0.0.0/0")
    error_message = "authorized_cidrs must not contain 0.0.0.0/0 - that exposes the control plane to the whole internet."
  }
}

variable "machine_type" {
  description = "Machine type for default node pool"
  type        = string
  default     = "e2-standard-4"
}

variable "gpu_machine_type" {
  description = "Machine type for GPU node pool"
  type        = string
  default     = "n1-standard-8"
}

variable "gpu_type" {
  description = "GPU accelerator type"
  type        = string
  default     = "nvidia-tesla-t4"
}

variable "domain_name" {
  description = "Domain name for the application"
  type        = string
  default     = ""
}
