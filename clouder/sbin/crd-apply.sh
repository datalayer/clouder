#!/usr/bin/env bash

# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

echo -e $BOLD$YELLOW"Apply CRD"$NOCOLOR$NOBOLD
echo

kubectl apply -f $CLOUDER_SBIN/../operator/crd/sshkeys.yaml
