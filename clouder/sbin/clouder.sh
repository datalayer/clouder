#!/usr/bin/env bash

# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

export CLOUDER_SBIN="$( cd "$( dirname "$0" )" && pwd )"

INITIAL_PATH=$PATH
export PATH=$CLOUDER_SBIN:$PATH

source $CLOUDER_SBIN/cli.sh
source $CLOUDER_SBIN/os.sh

$CLOUDER_SBIN/header.sh "$@"

# if [ $# == 0 ] ; then
#   exit 0;
# fi

$CLOUDER_SBIN/$1 "${@:2}"
FLAG=$?

PATH=$INITIAL_PATH

exit $FLAG
