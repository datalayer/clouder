# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

echo $BOLD$YELLOW"Create a Kind cluster with Ingress Nginx and Docker Registry"$NOCOLOR$NOBOLD

echo

set -o errexit

"""
cat <<EOF | kind create cluster --config=-
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
- role: control-plane
  kubeadmConfigPatches:
  - |
    kind: InitConfiguration
    nodeRegistration:
      kubeletExtraArgs:
        node-labels: "ingress-ready=true"
  extraPortMappings:
  - containerPort: 80
    hostPort: 80
    protocol: TCP
  - containerPort: 443
    hostPort: 443
    protocol: TCP
EOF
helm repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/
helm repo update
helm upgrade --install metrics-server metrics-server/metrics-server --set 'args[0]=--kubelet-insecure-tls' --wait --timeout 5m0s
VERSION=controller-v1.0.3
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/$VERSION/deploy/static/provider/kind/deploy.yaml
kubectl wait --namespace ingress-nginx --for=condition=ready pod --selector=app.kubernetes.io/component=controller --timeout=5m0s
"""

# Create registry container unless it already exists.
REGISTRY_NAME='kind-registry'
REGISTRY_PORT='5001'
if [ "$(docker inspect -f '{{.State.Running}}' "${REGISTRY_NAME}" 2>/dev/null || true)" != 'true' ]; then
  docker run \
    -d --restart=always -p "127.0.0.1:${REGISTRY_PORT}:5000" --name "${REGISTRY_NAME}" \
    registry:2
fi

# Create a cluster with the local registry enabled in containerd.
cat <<EOF | kind create cluster --config=-
# three node (two workers) cluster config
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
- role: control-plane
- role: worker
- role: worker
containerdConfigPatches:
- |-
  [plugins."io.containerd.grpc.v1.cri".registry.mirrors."localhost:${REGISTRY_PORT}"]
    endpoint = ["http://${REGISTRY_NAME}:5000"]
EOF

sleep 5

# kubectl label node kind-worker ingress-ready=true
# kubectl label node kind-worker2 ingress-ready=true

kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml

echo
echo Waiting on Ingress Nginx controller...

kubectl wait --namespace ingress-nginx \
  --for=condition=ready pod \
  --selector=app.kubernetes.io/component=controller \
  --timeout=90s

# Connect the registry to the cluster network if not already connected.
if [ "$(docker inspect -f='{{json .NetworkSettings.Networks.kind}}' "${REGISTRY_NAME}")" = 'null' ]; then
  docker network connect "kind" "${REGISTRY_NAME}"
fi

# Create a configmap for the local registry.
# https://github.com/kubernetes/enhancements/tree/master/keps/sig-cluster-lifecycle/generic/1755-communicating-a-local-registry
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: local-registry-hosting
  namespace: kube-public
data:
  localRegistryHosting.v1: |
    host: "localhost:${REGISTRY_PORT}"
    help: "https://kind.sigs.k8s.io/docs/user/local-registry/"
EOF

echo """
Using the Registry
------------------

The registry can be used like this.

- First we'll pull an image docker pull gcr.io/google-samples/hello-app:1.0
- Then we'll tag the image to use the local registry docker tag gcr.io/google-samples/hello-app:1.0 localhost:5001/hello-app:1.0
- Then we'll push it to the registry docker push localhost:5001/hello-app:1.0
- And now we can use the image kubectl create deployment hello-server --image=localhost:5001/hello-app:1.0
- If you build your own image and tag it like localhost:5001/image:foo and then use it in kubernetes as localhost:5001/image:foo. And use it from inside of your cluster application as kind-registry:5000.
"""

echo
echo https://kind.sigs.k8s.io/docs/user/ingress/#using-ingress
echo https://kind.sigs.k8s.io/docs/user/local-registry
echo
