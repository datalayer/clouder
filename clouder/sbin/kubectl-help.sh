#!/usr/bin/env bash

# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

echo -e $BOLD$YELLOW"Kubectl Help"$NOCOLOR$NOBOLD
echo

kubectl --help

echo -e $YELLOW"""
kubectl version
kubectl config get-contexts
kubectl config use-context <CONTEXT-NAME>
kubectl config set-context $(kubectl config current-context) --namespace=<YOUR-NAMESPACE>
kubectl config current-context
kubectl cluster-info
$NOCOLOR"""
