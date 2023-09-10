#!/usr/bin/env bash

# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

echo -e $BOLD$YELLOW"Minikube Help"$NOCOLOR$NOBOLD
echo

minikube help

echo -e $YELLOW"""
eval \$(minikube docker-env)
docker ps

minikube logs
minikube logs | grep ServiceClusterIPRange

kubectl run hello-minikube --image=gcr.io/google_containers/echoserver:1.4 --port=8080
kubectl expose deployment hello-minikube --type=NodePort
curl \$(minikube service hello-minikube --url)
kubectl delete service,deployment hello-minikube

clouder k8s-dashboard
kubectl describe rc k8s-dashboard --namespace=kube-system
"""
