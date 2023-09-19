#!/usr/bin/env bash

# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

echo -e $BOLD$YELLOW"Deploying Helm..."$NOCOLOR$NOBOLD
echo

helm_ins() {
  echo
  # kubectl -n kube-system create serviceaccount tiller
  # kubectl create clusterrolebinding tiller --clusterrole cluster-admin --serviceaccount=kube-system:tiller
  # helm init --service-account=tiller
}

helm_del() {
  echo
  # kubectl -n kube-system delete deployment tiller-deploy
  # kubectl delete clusterrolebinding tiller
  # kubectl -n kube-system delete serviceaccount tiller
}

deploy_on_k8s() {
    # Set up a ServiceAccount for use by Tiller, the server side component of helm.
    # kubectl -n kube-system create sa tiller
    # Give the ServiceAccount RBAC full permissions to manage the cluster.
    # While most clusters have RBAC enabled and you need this line, you must skip this step if your kubernetes cluster does not have RBAC enabled (for example, if you are using Azure AKS).
    # kubectl create clusterrolebinding tiller --clusterrole cluster-admin --serviceaccount=kube-system:tiller
    cat << EOF | kubectl apply -f -
apiVersion: v1
kind: ServiceAccount
metadata:
  name: tiller
  namespace: kube-system
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: tiller
  namespace: kube-system
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: cluster-admin
subjects:
- kind: ServiceAccount
  name: tiller
  namespace: kube-system
EOF

    # Set up Helm on Kubernetes 1.16+
    # https://github.com/helm/helm/issues/6374#issuecomment-533427268
    # https://stackoverflow.com/questions/58075103/error-error-installing-the-server-could-not-find-the-requested-resource-helm-k
#    helm init --service-account tiller --override spec.selector.matchLabels.'name'='tiller',spec.selector.matchLabels.'app'='helm' --output yaml | sed 's@apiVersion: extensions/v1beta1@apiVersion: apps/v1@' | kubectl apply -f -

    # Set up Helm on Kubernetes 1.15-
    # helm init --service-account tiller
    
    # Ensure that tiller is secure from access inside the cluster.
    kubectl --namespace=kube-system patch deployment tiller-deploy --type=json --patch='[{"op": "add", "path": "/spec/template/spec/containers/0/command", "value": ["/tiller", "--listen=localhost:44134"]}]'

}

add_helm_repos() {
#    helm repo add incubator https://kubernetes-charts-incubator.storage.googleapis.com
    helm repo add codecentric https://codecentric.github.io/helm-charts
    helm repo add incubator https://charts.helm.sh/incubator
    helm repo add jetstack https://charts.jetstack.io
    helm repo add jupyterhub https://hub.jupyter.org/helm-chart
    helm repo add k8s-dashboard https://kubernetes.github.io/dashboard
    helm repo add minio https://operator.min.io
    helm repo add stable https://charts.helm.sh/stable
    helm repo add apache-solr https://solr.apache.org/charts
    helm repo add k8ssandra https://helm.k8ssandra.io/stable
    helm repo add traefik https://helm.traefik.io/traefik
    helm repo add bitnami https://charts.bitnami.com/bitnami
    helm repo add spark-operator https://googlecloudplatform.github.io/spark-on-k8s-operator
    helm repo update
}

# deploy_on_k8s
add_helm_repos
echo
# CLOUDER_SHOW_HEADER=false dla helm-status
