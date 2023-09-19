#!/usr/bin/env bash

# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

echo -e $BOLD$YELLOW"Stopping Minikube on Local Node"$NOCOLOR$NOBOLD
echo

CLOUDER_SKIP_HEADER=true clouder minikube-stop-novm "$@"

sudo rm -fr /var/lib/localkube
sudo rm -fr /var/lib/kubelet

sudo mkdir /var/lib/localkube
sudo chown -R datalayer:datalayer /var/lib/localkube

sudo rm -fr /var/lib/etcd
sudo rm -fr /etc/kubernetes

sudo mkdir /etc/kubernetes
sudo chown -R datalayer:datalayer /etc/kubernetes
