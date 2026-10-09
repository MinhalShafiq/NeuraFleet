output "cluster_endpoint" {
  description = "GKE cluster endpoint"
  value       = module.k8s.cluster_endpoint
  sensitive   = true
}

output "cluster_ca_certificate" {
  description = "GKE cluster CA certificate"
  value       = module.k8s.cluster_ca_certificate
  sensitive   = true
}

output "storage_bucket_name" {
  description = "Name of the point cloud storage bucket"
  value       = module.storage.pointcloud_bucket_name
}

output "vpc_network_name" {
  description = "VPC network name"
  value       = module.networking.network_name
}

output "gpu_node_pool_name" {
  description = "GPU node pool name"
  value       = length(module.gpu) > 0 ? module.gpu[0].gpu_node_pool_name : null
}
