output "gpu_node_pool_name" {
  description = "GPU node pool name"
  value       = google_container_node_pool.gpu.name
}

output "gpu_node_pool_id" {
  description = "GPU node pool ID"
  value       = google_container_node_pool.gpu.id
}
