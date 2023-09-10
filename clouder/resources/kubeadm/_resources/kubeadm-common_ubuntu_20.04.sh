#!/usr/bin/env sh

sleep 5

sudo apt-mark hold linux-image-generic linux-headers-generic

# Update the apt package index and install packages needed to use the Kubernetes apt repository.
sudo apt update
sudo apt install -y apt-transport-https ca-certificates curl net-tools

# https://kubernetes.io/docs/setup/production-environment/container-runtimes/#containerd
sudo apt install -y containerd
sudo mkdir /etc/containerd
sudo containerd config default > /etc/containerd/config.toml
sudo sed -i 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml
sudo sed -i 's#sandbox_image = "registry.k8s.io/pause:3.5"#sandbox_image = "registry.k8s.io/pause:3.2"#' /etc/containerd/config.toml
sudo systemctl restart containerd

# https://kubernetes.io/docs/setup/production-environment/container-runtimes/#install-and-configure-prerequisites
cat <<EOF | sudo tee /etc/modules-load.d/k8s.conf
overlay
br_netfilter
EOF
sudo modprobe overlay
sudo modprobe br_netfilter
lsmod | grep br_netfilter

# sysctl params required by setup, params persist across reboots.
cat <<EOF | sudo tee /etc/sysctl.d/k8s.conf
net.bridge.bridge-nf-call-iptables  = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward                 = 1
EOF
# Apply sysctl params without reboot
sudo sysctl --system

# Download the Google Cloud public signing key.
sudo curl -fsSLo /usr/share/keyrings/kubernetes-archive-keyring.gpg https://packages.cloud.google.com/apt/doc/apt-key.gpg
# Add the Kubernetes apt repository.
echo "deb [signed-by=/usr/share/keyrings/kubernetes-archive-keyring.gpg] https://apt.kubernetes.io/ kubernetes-xenial main" | sudo tee /etc/apt/sources.list.d/kubernetes.list
# Update apt package index, install kubelet, kubeadm and kubectl, and pin their version.
sudo apt update
sudo apt install -y kubelet kubeadm kubectl
sudo apt-mark hold kubelet kubeadm kubectl

alias k='kubectl'

# kubeadm config images pull

# curl -Lo /tmp/helm-v${DATALAYER_HELM_VERSION}-linux-amd64.tar.gz https://get.helm.sh/helm-v${DATALAYER_HELM_VERSION}-linux-amd64.tar.gz \
#    && tar xvfz /tmp/helm-v${DATALAYER_HELM_VERSION}-linux-amd64.tar.gz -C /tmp \
#    && mv /tmp/linux-amd64/helm /usr/local/bin \
#    && chmod +x /usr/local/bin/helm

# helm repo add cpo https://kubernetes.github.io/cloud-provider-openstack
# helm repo update
# helm install openstack-ccm cpo/openstack-cloud-controller-manager --values openstack-ccm.yaml

mv /tmp/openstack-api-ca.cert /etc/ssl/certs/openstack-api-ca.cert

mkdir /etc/config || true
mv /tmp/cloud.conf /etc/config/cloud.conf
mkdir /etc/kubernetes || true
cp /etc/config/cloud.conf /etc/kubernetes/cloud-config
