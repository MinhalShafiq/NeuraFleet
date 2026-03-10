resource "google_container_node_pool" "gpu" {
  name     = "${var.cluster_name}-gpu-pool"
  location = var.zone
  cluster  = var.cluster_name

  autoscaling {
    min_node_count = 0
    max_node_count = var.gpu_node_count
  }

  node_config {
    machine_type = var.gpu_machine_type
    disk_size_gb = 200
    disk_type    = "pd-ssd"

    # Preemptible for cost savings in non-prod
    preemptible = var.environment != "prod"

    guest_accelerator {
      type  = var.gpu_type
      count = 1

      gpu_driver_installation_config {
        gpu_driver_version = "DEFAULT"
      }
    }

    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform",
      "https://www.googleapis.com/auth/devstorage.read_only",
      "https://www.googleapis.com/auth/logging.write",
      "https://www.googleapis.com/auth/monitoring",
    ]

    labels = {
      environment = var.environment
      pool        = "gpu"
      gpu         = "true"
      workload    = "inference"
    }

    taint {
      key    = "nvidia.com/gpu"
      value  = "present"
      effect = "NO_SCHEDULE"
    }

    tags = ["gke-node", "gpu-node", "neurafleet-${var.environment}"]

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }
}
