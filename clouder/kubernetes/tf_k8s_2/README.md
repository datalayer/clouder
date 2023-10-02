[![Datalayer](https://assets.datalayer.tech/datalayer-25.svg)](https://datalayer.io)

# OVHcloud K8S Cluster with Terraform Kubeadm

```bash
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
MASTER_IP=$(terraform output --json | jq '."dla-master-floating-ip".value[0]' --raw-output)
echo $MASTER_IP
ssh -oStrictHostKeyChecking=no centos@$MASTER_IP
# ./../_resources/kubeadm-master.sh
WORKER_IP=$(terraform output --json | jq '."dla-worker-floating-ip".value[0]' --raw-output)
echo $WORKER_IP
ssh -oStrictHostKeyChecking=no centos@$WORKER_IP
# ./../_resources/kubeadm-worker.sh
```

```bash
terraform destroy -auto-approve
```
