#!/usr/bin/env bash

# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

echo -e $BOLD$YELLOW"Minikube Status"$NOCOLOR$NOBOLD
echo

DATALAYER_SKIP_HEADER=true clouder k8s-status "$@"

# minikube get-k8s-versions
minikube version
minikube update-check
minikube ip
minikube addons list
minikube service list
minikube status || true
