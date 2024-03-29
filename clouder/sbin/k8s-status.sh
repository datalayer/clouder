#!/usr/bin/env bash

# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

echo -e $BOLD$YELLOW"Kubernetes Status"$NOCOLOR$NOBOLD
echo

# This for loop waits until kubectl can access the api server that minikube has created.
for i in {1..150} # timeout for 5 minutes.
do
   kubectl get pods &> /dev/null
   if [ $? -ne 1 ]; then
      break
  fi
  echo 'Error while getting the pods - Sleeping for 2 seconds...'
  sleep 2
done
# kubectl commands are now able to interact with minikube cluster.
echo -e $WHITE_BCK"CONFIG"$NOCOLOR
echo
kubectl config view
echo
echo -e $WHITE_BCK"NODES"$NOCOLOR
echo
kubectl get nodes
echo
echo -e $WHITE_BCK"SERVICES"$NOCOLOR
echo
kubectl get svc --all-namespaces
echo
echo -e $WHITE_BCK"HELM"$NOCOLOR
echo
helm ls
echo
echo -e $WHITE_BCK"PODS"$NOCOLOR
echo
kubectl get pods --all-namespaces
echo
