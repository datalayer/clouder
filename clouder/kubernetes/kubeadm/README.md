[![Datalayer](https://assets.datalayer.tech/datalayer-25.svg)](https://datalayer.io)

# OVHcloud Kubernetes Cluster with Terraform

```bash
terraform init
terraform plan
terraform apply -auto-approve
terraform output
terraform output kubeconfig > ./kubeconfig/kubeconfig.yaml
terraform destroy -auto-approve
```

```bash
export KUBECONFIG=$PWD/kubeconfig/kubeconfig.yaml
kubectl --kubeconfig=./kubeconfig/kubeconfig.yaml get nodes
kubectl --kubeconfig=./kubeconfig/kubeconfig.yaml get nodepool
kubectl --kubeconfig=./kubeconfig/kubeconfig.yaml get all -A
```
