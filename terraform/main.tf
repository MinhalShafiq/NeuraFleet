module "networking" {
  source = "./modules/networking"

  project_id  = var.project_id
  region      = var.region
  environment = var.environment
}

module "k8s" {
  source = "./modules/k8s"

  project_id        = var.project_id
  region            = var.region
  zone              = var.zone
  environment       = var.environment
  cluster_name      = var.cluster_name
  node_count        = var.node_count
  machine_type      = var.machine_type
  network_self_link = module.networking.network_self_link
  subnet_self_link  = module.networking.subnet_self_link

  depends_on = [module.networking]
}

module "storage" {
  source = "./modules/storage"

  project_id  = var.project_id
  region      = var.region
  environment = var.environment
}

module "gpu" {
  source = "./modules/gpu"

  project_id       = var.project_id
  zone             = var.zone
  environment      = var.environment
  cluster_name     = module.k8s.cluster_name
  gpu_node_count   = var.gpu_node_count
  gpu_machine_type = var.gpu_machine_type
  gpu_type         = var.gpu_type

  depends_on = [module.k8s]
}
