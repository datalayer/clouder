#!/usr/bin/env sh

./kubeadm-common_centos_7.sh

# vi ./kubeadm-worker.yaml
kubeadm join --config=./kubeadm-worker.yaml
