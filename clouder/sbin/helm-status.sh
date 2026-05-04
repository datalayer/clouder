#!/usr/bin/env bash

# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

echo -e $BOLD$YELLOW"Helm Status"$NOCOLOR$NOBOLD
echo

helm version

echo

CLOUDER_SHOW_HEADER=false clouder helm-ls
