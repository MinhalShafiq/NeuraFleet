project_id       = "neurafleet-staging"
region           = "us-central1"
zone             = "us-central1-a"
environment      = "staging"
cluster_name     = "neurafleet"
node_count       = 3
gpu_node_count   = 0 # no GPU workloads; see variables.tf
machine_type     = "e2-standard-4"
gpu_machine_type = "n1-standard-8"
gpu_type         = "nvidia-tesla-t4"
domain_name      = "staging.neurafleet.io"

# authorized_cidrs is REQUIRED and intentionally not set here (it is site-specific).
# Put it in an untracked environments/staging.local.tfvars and add -var-file=environments/staging.local.tfvars
# (see the description in variables.tf).
