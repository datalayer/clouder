#!/usr/bin/env bash

# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

echo -e $BOLD$YELLOW"Helm ls"$NOCOLOR$NOBOLD
echo

helm ls --all-namespaces
