#!/usr/bin/env bash

# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

echo -e $BOLD$YELLOW"Datalayer Helm Status"$NOCOLOR$NOBOLD
echo

helm version

echo
DATALAYER_SHOW_HEADER=false dla helm-ls
