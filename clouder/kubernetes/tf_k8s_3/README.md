[![Datalayer](https://assets.datalayer.tech/datalayer-25.svg)](https://datalayer.io)

# OVHcloud K8S Cluster with Terraform Kubeadm

```bash
cd tf
terraform init
terraform plan
terraform apply -auto-approve
terraform output
```

```bash
openstack keypair list
openstack router list
openstack server list
openstack volume list
openstack loadbalancer list
```

```bash
terraform destroy -auto-approve
```

```bash
export KUBECONFIG=$PWD/kubeconfig/kubeconfig.yaml
kubectl --kubeconfig=./kubeconfig/kubeconfig.yaml get nodes
kubectl --kubeconfig=./kubeconfig/kubeconfig.yaml get nodepool
kubectl --kubeconfig=./kubeconfig/kubeconfig.yaml get all -A
```
