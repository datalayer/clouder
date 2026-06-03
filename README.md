[![Datalayer](https://assets.datalayer.tech/datalayer-25.svg)](https://datalayer.io)

[![Become a Sponsor](https://img.shields.io/static/v1?label=Become%20a%20Sponsor&message=%E2%9D%A4&logo=GitHub&style=flat&color=1ABC9C)](https://github.com/sponsors/datalayer)

# ☁️ Clouder

> Create, manage and share Kubernetes clusters.

Clouder is a CLI an Python package to interact with cloud services. Devops can manage Kubernetes clusters, SSH Keys, virtual machines, S3 buckets on multiple clouds in a seamless way. Clouder provides advanced collaboration and cost optimisation features:

- Create and monitor Kubernetes clusters.
- Manage Helm deployments.
- Schedule the size of the Kubernetes clusters.
- Share the cluster and give controlled access to other users.
- Take backup and restore for disaster recovery.

It supports alpha features of Kubernetes like [Container checkpoint and restore](https://criu.org/Kubernetes) (CRIU)

Azure, AWS and OVHcloud are supported for now. Support of other cloud is planned in subsequent releases.

Read more on the [Clouder documentation](https://clouder.sh) website.

## Kubeadm Commands

Common lifecycle commands for kubeadm clusters:

```bash
# Scale cluster workers
clouder kubeadm scale r1 --workers 10

# Scale workers with larger OS disks for higher ephemeral-storage on each node
clouder kubeadm scale r1 --workers 10 --os-disk-size-gb 128

# Prune unhealthy worker nodes/VMs (interactive confirmation)
clouder kubeadm prune r1

# Prune unhealthy worker nodes/VMs without prompt
clouder kubeadm prune r1 --force
```

### Prune Command

`clouder kubeadm prune <cluster>` identifies unhealthy worker resources for the cluster and asks for confirmation before forced cleanup:

- Kubernetes worker nodes where `Ready != True`
- Azure worker VMs where `provisioning_state != Succeeded`

When confirmed, it force-deletes matching Kubernetes node objects and Azure VMs.
