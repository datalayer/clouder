#!/usr/bin/env bash

# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

echo -e $BOLD$YELLOW"Datalayer Helm Charts"$NOCOLOR$NOBOLD
echo

function build_helm_chart() {
  echo -e $YELLOW"Building Helm Chart ["$GREEN$1$YELLOW"]"$NOCOLOR
  echo
  rm -fr $CLOUDER_HOME/etc/helm/$1/*tmpcharts || true
  rm -fr $CLOUDER_HOME/etc/helm/$1/*.lock || true
  helm dependency build $CLOUDER_HOME/etc/helm/$1
}

CMDS="$1"

if [ -z "$CMDS" ]; then
  CMDS='tech/k8s-dashboard,run/solr'
fi

IFS=',' read -ra CMD_SPLITS <<< "$CMDS"
for i in "${CMD_SPLITS[@]}"; do
  build_helm_chart $i
  echo
done
