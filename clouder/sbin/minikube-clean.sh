#!/usr/bin/env bash

# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

echo -e $BOLD$YELLOW"Cleaning Minikube"$NOCOLOR$NOBOLD
echo

docker rm -f $(docker ps -a -q)
sudo rm -fr $HOME/.kube $HOME/.minikube /var/lib/localkube /var/lib/kubelet; sudo mkdir /var/lib/localkube; sudo chown -R datalayer:datalayer /var/lib/localkube
sudo rm -fr /etc/kubernetes; sudo mkdir /etc/kubernetes; sudo chown -R datalayer:datalayer /etc/kubernetes
