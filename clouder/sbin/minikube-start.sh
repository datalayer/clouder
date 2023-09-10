#!/usr/bin/env bash

# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

echo -e $BOLD$YELLOW"Starting Minikube"$NOCOLOR$NOBOLD
echo

minikube config set kubernetes-version v${DATALAYER_K8S_VERSION}
minikube config set bootstrapper kubeadm
minikube config set cpus 8
minikube config set memory 10192
minikube config set disk-size 100g
# minikube config set WantReportErrorPrompt false

# --container-runtime containerd
# --container-runtime rkt

# --iso-url https://github.com/coreos/minikube-iso/releases/download/v0.0.5/minikube-v0.0.5.iso
# --network-plugin cni
# --allocate-node-cidrs true
# --cluster-cidr 10.244.0.0/16
# --pod-network-cidr 10.244.0.0/16
# --hyperv-virtual-switch Minikube
# --v 6

# --extra-config apiserver.EnableInsecureLogin.Mode=false
# --extra-config apiserver.enable-admission-plugins="LimitRanger,NamespaceExists,NamespaceLifecycle,ResourceQuota,ServiceAccount,DefaultStorageClass,MutatingAdmissionWebhook"
# --extra-config apiserver.Authorization.Mode=RBAC
# --extra-config apiserver.v=10
# --extra-config apiserver.ServiceClusterIPRange=10.1.0.1/16
# --extra-config kubelet.max-pods=100
# --extra-config kubelet.container-runtime=remote
# --extra-config kubelet.container-runtime-endpoint=unix:///run/containerd/containerd.sock
# --extra-config kubelet.image-service-endpoint=unix:///run/containerd/containerd.sock

linux_conf() {
    minikube config set vm-driver kvm2
}

macos_conf() {
    minikube config set vm-driver hyperkit
#      --logtostderr \
    minikube start \
      --listen-address 192.168.64.28 \
      --insecure-registry localhost:5000 \
      --network minikube \
      --embed-certs \
      --v 0
}

case "${OS}" in
    LINUX)     linux_conf;;
    MACOS)     macos_conf;;
    *)         echo "Unsupported operating system ${OS}"
esac

# mkdir -p ~/.minikube/files/etc && \
#   cp /etc/localtime  ~/.minikube/files/etc

#    --logtostderr \
#    --v 0 \
# minikube start \
#     --insecure-registry localhost:5000

# https://kubernetes.io/docs/reference/access-authn-authz/rbac
# Many add-ons currently run as the “default” service account in the kube-system namespace.
# To allow those add-ons to run with super-user access, grant cluster-admin permissions to the “default” service account in the kube-system namespace.
# Note: Enabling this means the kube-system namespace contains secrets that grant super-user access to the API.
kubectl create clusterrolebinding add-on-cluster-admin \
  --clusterrole=cluster-admin \
  --serviceaccount=kube-system:default

minikube addons disable registry-creds
minikube addons enable dashboard
minikube addons enable ingress
minikube addons enable ingress-dns
minikube addons enable registry
minikube addons enable storage-provisioner

echo
DATALAYER_SKIP_HEADER=true clouder minikube-help "$@"
DATALAYER_SKIP_HEADER=true clouder minikube-status "$@"
echo
DATALAYER_SKIP_HEADER=true clouder k8s-status "$@"

echo -e $YELLOW$BOLD"""
Helm$NOBOLD

clouder helm-deploy
$BOLD
K8S Dashboard$NOBOLD

dla up minikube k8s-dashboard
clouder k8s-dashboard
$BOLD
Use Minikube Docker Process$NOBOLD

eval $(minikube docker-env)
"""$NOCOLOR
