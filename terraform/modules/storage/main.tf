# Point cloud data bucket
resource "google_storage_bucket" "pointcloud" {
  name          = "neurafleet-pointcloud-${var.environment}"
  location      = var.region
  force_destroy = var.environment != "prod"
  project       = var.project_id

  uniform_bucket_level_access = true

  versioning {
    enabled = false
  }

  lifecycle_rule {
    condition {
      age = 90
    }
    action {
      type = "Delete"
    }
  }

  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }

  labels = {
    environment = var.environment
    project     = "neurafleet"
    data_type   = "pointcloud"
  }
}

# Model artifacts bucket
resource "google_storage_bucket" "models" {
  name          = "neurafleet-models-${var.environment}"
  location      = var.region
  force_destroy = var.environment != "prod"
  project       = var.project_id

  uniform_bucket_level_access = true

  versioning {
    enabled = true
  }

  labels = {
    environment = var.environment
    project     = "neurafleet"
    data_type   = "models"
  }
}

# Service account for storage access
resource "google_service_account" "storage_sa" {
  account_id   = "neurafleet-storage-${var.environment}"
  display_name = "NeuraFleet Storage Service Account (${var.environment})"
  project      = var.project_id
}

# IAM binding for point cloud bucket
resource "google_storage_bucket_iam_member" "pointcloud_admin" {
  bucket = google_storage_bucket.pointcloud.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.storage_sa.email}"
}

# IAM binding for models bucket
resource "google_storage_bucket_iam_member" "models_admin" {
  bucket = google_storage_bucket.models.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.storage_sa.email}"
}
