output "kubeconfig" {
  value = ovh_cloud_project_kube.dla_kube_cluster.kubeconfig
  sensitive = true
}