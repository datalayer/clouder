# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

export CLOUDER_SBIN="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

INITIAL_PATH=$PATH
export PATH=$CLOUDER_SBIN:$PATH

source $CLOUDER_SBIN/cli.sh

$CLOUDER_SBIN/header.sh "$@"

if [ $# == 0 ] ; then
#  echo $USAGE
  exit 0;
fi

$1 "${@:2}"
FLAG=$?

PATH=$INITIAL_PATH

exit $FLAG
