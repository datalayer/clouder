[![Datalayer](https://assets.datalayer.tech/datalayer-25.svg)](https://datalayer.io)

# OVHcloud K8S Cluster with Terraform Kubeadm

```bash
cd tf
terraform init
terraform plan
terraform apply -auto-approve
# https://github.com/terraform-provider-openstack/terraform-provider-openstack/issues/602
terraform apply -auto-approve -parallelism=1
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
