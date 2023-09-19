#!/usr/bin/env bash

# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

CLOUDER_SKIP_HEADER=true clouder minikube-stop

echo
echo -e $BOLD$YELLOW"Removing Minikube..."$NOCOLOR$NOBOLD
echo

minikube delete || true

delete_on_linux() {
    virsh list
    virsh shutdown minikube || true
    virsh undefine minikube || true
}

delete_on_macos() {
    echo
}

case "${OS}" in
    LINUX)     delete_on_linux;;
    MACOS)     delete_on_macos;;
    *)         echo "Unkown machine ${MACHINE}"
esac

rm -fr ~/.minikube

# docker rm -f $(docker ps -a -q) || true

# echo
# CLOUDER_SKIP_HEADER=true clouder minikube-status "$@"
