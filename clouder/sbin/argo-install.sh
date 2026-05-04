#!/usr/bin/env bash

# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

echo -e $BOLD$YELLOW"Installing Argo..."$NOCOLOR$NOBOLD
echo

install_argo_on_linux() {
    curl -sLO https://github.com/argoproj/argo-workflows/releases/download/v3.5.5/argo-linux-amd64.gz && \
        gunzip argo-linux-amd64.gz && \
        chmod +x argo-linux-amd64 && \
        mv ./argo-linux-amd64 /usr/local/bin/argo && \
        argo version
}

install_argo_on_macos() {
    curl -sLO https://github.com/argoproj/argo-workflows/releases/download/v3.5.5/argo-darwin-amd64.gz && \
        gunzip argo-darwin-amd64.gz && \
        chmod +x argo-darwin-amd64 && \
        mv ./argo-darwin-amd64 /usr/local/bin/argo && \
        argo version
}

case "${OS}" in
    LINUX)     install_argo_on_linux;;
    MACOS)     install_argo_on_macos;;
    *)         echo "Unsupported operating system ${OS}"
esac
