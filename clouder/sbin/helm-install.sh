#!/usr/bin/env bash

# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

echo -e $BOLD$YELLOW"Installing Helm..."$NOCOLOR$NOBOLD
echo

CLOUDER_HELM_VERSION=3.13.2

install_helm_on_linux() {
    curl -Lo /tmp/helm-v${CLOUDER_HELM_VERSION}-linux-amd64.tar.gz https://get.helm.sh/helm-v${CLOUDER_HELM_VERSION}-linux-amd64.tar.gz \
        && tar xvfz /tmp/helm-v${CLOUDER_HELM_VERSION}-linux-amd64.tar.gz -C /tmp \
        && mv /tmp/linux-amd64/helm /usr/local/bin \
        && chmod +x /usr/local/bin/helm
}

install_helm_on_macos() {
    curl -Lo /tmp/helm-v${CLOUDER_HELM_VERSION}-darwin-amd64.tar.gz https://get.helm.sh/helm-v${CLOUDER_HELM_VERSION}-darwin-amd64.tar.gz \
        && tar xvfz /tmp/helm-v${CLOUDER_HELM_VERSION}-darwin-amd64.tar.gz -C /tmp \
        && mv /tmp/darwin-amd64/helm /usr/local/bin \
        && chmod +x /usr/local/bin/helm
}

case "${OS}" in
    LINUX)     install_helm_on_linux;;
    MACOS)     install_helm_on_macos;;
    *)         echo "Unsupported operating system ${OS}"
esac

# helm init --client-only
# echo
# CLOUDER_SHOW_HEADER=false dla helm-status

helm repo add datalayer https://helm.datalayer.io/stable | true
helm repo add datalayer-incubator https://helm.datalayer.io/incubator | true
helm repo add datalayer-examples https://helm.datalayer.io/examples | true
helm repo add datalayer-experiments https://helm.datalayer.io/experiments | true
helm repo update
