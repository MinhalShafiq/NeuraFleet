terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.10"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 5.10"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 3.3"
    }
  }

  backend "gcs" {
    bucket = "neurafleet-terraform-state"
    prefix = "terraform/state"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}

data "google_client_config" "default" {}

# Provider configuration lives in the root module (not inside modules/k8s): a module that
# declares its own provider is a "legacy module" and cannot use depends_on/count/for_each.
provider "kubernetes" {
  host                   = "https://${module.k8s.cluster_endpoint}"
  token                  = data.google_client_config.default.access_token
  cluster_ca_certificate = base64decode(module.k8s.cluster_ca_certificate)
}

