# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

echo $BOLD$YELLOW"Installing Kind"$NOCOLOR$NOBOLD
echo

source $CLOUDER_SBIN/os.sh

function install_on_linux() {
  # For AMD64 / x86_64
  [ $(uname -m) = x86_64 ] && curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.20.0/kind-linux-amd64
  # For ARM64
  [ $(uname -m) = aarch64 ] && curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.20.0/kind-linux-arm64
  chmod +x ./kind
  sudo mv ./kind /usr/local/bin/kind
}

function install_on_macos() {
  # For Intel Macs
  [ $(uname -m) = x86_64 ] && curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.20.0/kind-darwin-amd64
  # For M1 / ARM Macs
  [ $(uname -m) = arm64 ] && curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.20.0/kind-darwin-arm64
  chmod +x ./kind
  mv ./kind /usr/local/bin/kind
}

case "${OS}" in
    LINUX)     install_on_linux;;
    MACOS)     install_on_macos;;
    *)         echo "Unsupported operating system ${OS}"
esac

kind --help

"""
kind create cluster --name kind
kind get clusters
kubectl cluster-info --context kind-kind
docker exec -it kind-control-plane bash
kind load docker-image my-custom-image-0 my-custom-image-1 --name kind
docker exec -it kind-control-plane crictl images
kind delete cluster --name kind
"""
