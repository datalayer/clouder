#!/usr/bin/env bash

# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

echo -e $BOLD$YELLOW"Stopping Minikube on Local Node"$NOCOLOR$NOBOLD
echo

DATALAYER_SKIP_HEADER=true clouder minikube-help "$@"

sudo systemctl stop localkube
sudo systemctl disable localkube
sudo systemctl stop kubelet
sudo systemctl disable kubelet
