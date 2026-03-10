output "pointcloud_bucket_name" {
  description = "Point cloud data bucket name"
  value       = google_storage_bucket.pointcloud.name
}

output "models_bucket_name" {
  description = "Model artifacts bucket name"
  value       = google_storage_bucket.models.name
}

output "storage_service_account_email" {
  description = "Storage service account email"
  value       = google_service_account.storage_sa.email
}
