#!/usr/bin/env bash

# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

echo -e $BOLD$YELLOW"K8S Dashboard"$NOCOLOR$NOBOLD
echo

echo """open http://127.0.0.1:???/api/v1/namespaces/kube-system/services/http:kubernetes-dashboard:/proxy/#!/overview?namespace=default

kubectl proxy

open http://localhost:8001/api/v1/namespaces/kube-system/services/http:kubernetes-dashboard:/proxy/#!/login

Check existing secrets in kube-system namespace.

kubectl -n kube-system get secret

All secrets with type 'kubernetes.io/service-account-token' will allow to log in. Note that they have different privileges.

kubectl -n kube-system describe secret replicaset-controller-token-...

kubectl -n kube-system get secret | grep dashboard
kubectl -n kube-system describe secret kubernetes-dashboard-key-holder

# Launch a K8S proxy in another terminal to have easy access to the services.
http://localhost:8001/api/v1/namespaces/kube-system/services/http:kubernetes-dashboard:/proxy/#!/overview?namespace=_all
kubectl proxy
# or... launch Dashboard via minikube.
minikube dashboard
# or.. use port-forward.
https://localhost:8443
kubectl -n kube-system port-forward $(kubectl get pods -n kube-system -l "app.kubernetes.io/instance=k8s-dashboard" -o jsonpath="{.items[0].metadata.name}") 8443:8443
"""

# minikube dashboard

TOKEN=$(kubectl -n kube-system describe secret $(kubectl -n kube-system get secret | grep k8s-dashboard-kubernetes-dashboard-token | cut -d " " -f1) | grep "token:" | awk -F ":      " '{print $2}')

echo -e $YELLOW$BOLD"""AUTH TOKEN$NOBOLD

$TOKEN
$BOLD$GREEN
open https://localhost:8443$NOBOLD$NOCOLOR
"""
# open https://localhost:8443

kubectl -n kube-system port-forward $(kubectl get pods -n kube-system -l "app.kubernetes.io/component=kubernetes-dashboard,app.kubernetes.io/instance=k8s-dashboard" -o jsonpath="{.items[0].metadata.name}") 8443:8443
