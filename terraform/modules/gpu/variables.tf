variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "zone" {
  description = "GCP zone"
  type        = string
}

variable "environment" {
  description = "Environment name"
  type        = string
}

variable "cluster_name" {
  description = "GKE cluster name"
  type        = string
}

variable "gpu_node_count" {
  description = "Maximum number of GPU nodes"
  type        = number
}

variable "gpu_machine_type" {
  description = "Machine type for GPU nodes"
  type        = string
}

variable "gpu_type" {
  description = "GPU accelerator type"
  type        = string
}
