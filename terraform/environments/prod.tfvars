project_id       = "neurafleet-prod"
region           = "us-central1"
zone             = "us-central1-a"
environment      = "prod"
cluster_name     = "neurafleet"
node_count       = 5
gpu_node_count   = 0 # no GPU workloads; see variables.tf
machine_type     = "e2-standard-4"
gpu_machine_type = "n1-standard-8"
gpu_type         = "nvidia-tesla-t4"
domain_name      = "neurafleet.io"

# authorized_cidrs is REQUIRED and intentionally not set here (it is site-specific).
# Put it in an untracked environments/prod.local.tfvars and add -var-file=environments/prod.local.tfvars
# (see the description in variables.tf).
