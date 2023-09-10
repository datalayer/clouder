#!/usr/bin/env bash

# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

DATALAYER_SKIP_HEADER=true clouder minikube-stop

DATALAYER_SKIP_HEADER=true clouder minikube-start
