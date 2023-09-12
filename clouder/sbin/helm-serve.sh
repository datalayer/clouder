#!/usr/bin/env bash

# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

echo -e $BOLD$YELLOW"Serving Helm Repository..."$NOCOLOR$NOBOLD
echo

cd $DATALAYER_HOME/etc/helm

for chart in */ ; do
  helm package "$chart" .
done

nohup helm serve \
  --address 0.0.0.0:80 \
  --repo-path . &
