#!/usr/bin/env bash

# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

CLOUDER_SKIP_HEADER=true clouder minikube-stop

CLOUDER_SKIP_HEADER=true clouder minikube-start
