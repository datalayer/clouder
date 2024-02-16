#!/usr/bin/env bash

# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

echo -e $BOLD$YELLOW"Apply CRD"$NOCOLOR$NOBOLD
echo

export DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

echo $DIR

kubectl apply -f $DIR/crd.yml
echo
kubectl describe crd ssh-key.clouder.sh
