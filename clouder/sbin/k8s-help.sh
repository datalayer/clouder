#!/usr/bin/env bash

# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

echo -e $BOLD$YELLOW"Kubernetes Help"$NOCOLOR$NOBOLD
echo

echo -e $GREEN$BOLD"Kubernetes Commands"$NOBOLD$NOCOLOR
echo
echo -e "kubectl get nodes"
echo -e "kubectl get deployments"
echo -e "kubectl get pods"
echo -e "kubectl get svc"
echo -e "kubectl get svc --all-namespaces"
echo
echo -e "kubectl proxy"
echo

echo -e $GREEN$BOLD"Kubernetes Status"$NOBOLD$NOCOLOR
echo
echo -e "Kubernetes WEB Interface "$YELLOW"http://localhost:9090"$NOCOLOR" (Default username/password: kubernetes/kubernetes)"
echo
