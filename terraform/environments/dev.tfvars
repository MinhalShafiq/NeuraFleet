project_id       = "neurafleet-dev"
region           = "us-central1"
zone             = "us-central1-a"
environment      = "dev"
cluster_name     = "neurafleet"
node_count       = 2
gpu_node_count   = 0 # no GPU workloads; see variables.tf
machine_type     = "e2-standard-2"
gpu_machine_type = "n1-standard-4"
gpu_type         = "nvidia-tesla-t4"
domain_name      = "dev.neurafleet.io"

# authorized_cidrs is REQUIRED and intentionally not set here (it is site-specific).
# Put it in an untracked environments/dev.local.tfvars and add -var-file=environments/dev.local.tfvars
# (see the description in variables.tf).
