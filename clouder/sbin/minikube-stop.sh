#!/usr/bin/env bash

# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

echo -e $BOLD$YELLOW"Stopping Minikube..."$NOCOLOR$NOBOLD
echo

stop_on_linux() {
    minikube stop "$@"
}

stop_on_macos() {
    minikube stop "$@"
}

# minikube config set WantReportErrorPrompt false

case "${OS}" in
    LINUX)     stop_on_linux;;
    MACOS)     stop_on_macos;;
    *)         echo "Unsupported operating system ${OS}"
esac

# minikube status "$@"
echo
