#!/usr/bin/env bash

# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

echo -e $BOLD$YELLOW"Minikube Docker Env"$NOCOLOR$NOBOLD
echo

echo -e """Type the following in your shell to evaluate minikube docker-env.
$BOLD$GREEN
eval \$(minikube docker-env)
$NOCOLOR$NOBOLD
eval $(minikube docker-env)
"""
