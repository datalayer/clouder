#!/usr/bin/env bash

# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

echo -e $BOLD$YELLOW"Starting Minikube on Local Node"$NOCOLOR$NOBOLD
echo

export MINIKUBE_WANTUPDATENOTIFICATION=false
export MINIKUBE_WANTREPORTERRORPROMPT=false
export MINIKUBE_HOME=$HOME
export CHANGE_MINIKUBE_NONE_USER=true

# mkdir $HOME/.kube || true
# touch $HOME/.kube/config
# export KUBECONFIG=$HOME/.kube/config

sudo -E minikube start \
  --vm-driver=none \
  "$@"

CLOUDER_SKIP_HEADER=true clouder kubectl-check "$@"

CLOUDER_SKIP_HEADER=true clouder minikube-help "$@"
