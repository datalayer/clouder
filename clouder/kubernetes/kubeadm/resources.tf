resource "ovh_cloud_project_kube" "dla_kube_cluster" {
   provider     = ovh.ovh
   service_name = var.dla_env.service_name
   region       = var.dla_env.region
   name         = "datalayer_dev"
   version      = "1.25"
}

resource "ovh_cloud_project_kube_nodepool" "dla_node_pool" {
   provider     = ovh.ovh
   service_name = var.dla_env.service_name
   kube_id       = ovh_cloud_project_kube.dla_kube_cluster.id
   name          = "dla-pool-1" // "_" is not allowed.
   flavor_name   = "b2-7" # "b2-7" "b2-15" "c2-15" "t1-45" "t1-90"
   desired_nodes = 5
   max_nodes     = 100
   min_nodes     = 0
}

resource "local_file" "kubeconfig" {
  content     = ovh_cloud_project_kube.dla_kube_cluster.kubeconfig
  filename = "./kubeconfig/kubeconfig.yaml"
}
