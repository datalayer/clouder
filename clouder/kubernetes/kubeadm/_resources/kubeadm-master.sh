#!/usr/bin/env sh

./kubeadm-common_centos_7.sh

#############################
# Kubeadm init
#############################

kubeadm init --config=/tmp/kubeadm-master.yaml
# kubeadm token create --print-join-command
# kubeadm token list
# openssl x509 -pubkey -in /etc/kubernetes/pki/ca.crt | openssl rsa -pubin -outform der 2>/dev/null | \
#   openssl dgst -sha256 -hex | sed 's/^.* //'

#############################
# Kubeconfig
#############################

mkdir -p $HOME/.kube
sudo cp -i /etc/kubernetes/admin.conf $HOME/.kube/config
sudo chown $(id -u):$(id -g) $HOME/.kube/config
sudo cp -i /etc/kubernetes/admin.conf /tmp/kubeconfig.yaml
chown centos /tmp/kubeconfig.yaml

#############################
# Kubernetes Networking
#############################

kubectl apply -f https://github.com/weaveworks/weave/releases/download/v2.8.1/weave-daemonset-k8s.yaml

##########################
# OpenStack Cloud Provider
##########################

kubectl create secret -n kube-system generic cloud-config --from-file=/etc/config/cloud.conf

kubectl apply -f https://raw.githubusercontent.com/kubernetes/cloud-provider-openstack/v1.25.3/manifests/controller-manager/cloud-controller-manager-roles.yaml
kubectl apply -f https://raw.githubusercontent.com/kubernetes/cloud-provider-openstack/v1.25.3/manifests/controller-manager/cloud-controller-manager-role-bindings.yaml
kubectl apply -f https://raw.githubusercontent.com/kubernetes/cloud-provider-openstack/v1.25.3/manifests/controller-manager/openstack-cloud-controller-manager-ds.yaml

##########################
# OpenStack Cinder CSI
##########################

kubectl create secret -n kube-system generic cloud-config --from-literal=cloud.conf="$(cat /etc/kubernetes/cloud-config)" --dry-run=client -o yaml > cloud-config-secret.yaml
kubectl apply -f cloud-config-secret.yaml

kubectl apply -f https://raw.githubusercontent.com/kubernetes/cloud-provider-openstack/master/manifests/cinder-csi-plugin/cinder-csi-controllerplugin-rbac.yaml
kubectl apply -f https://github.com/kubernetes/cloud-provider-openstack/raw/master/manifests/cinder-csi-plugin/cinder-csi-nodeplugin-rbac.yaml

kubectl apply -f /etc/kubernetes/cinder-csi/cinder-csi-controllerplugin.yaml
kubectl apply -f /etc/kubernetes/cinder-csi/cinder-csi-nodeplugin.yaml
kubectl apply -f /etc/kubernetes/cinder-csi/csi-cinder-driver.yaml
kubectl apply -f /etc/kubernetes/cinder-csi/csi-cinder-storage-class.yaml

kubectl get pods -n kube-system | grep csi
kubectl get csidrivers -A
# kubectl get csidrivers.storage.k8s.io -A
kubectl get csinodes
kubectl get sc -A

######################################
# OpenStack Octavia Ingress Controller
######################################

mkdir /etc/kubernetes/octavia-ingress-controller

cat <<EOF > /etc/kubernetes/octavia-ingress-controller/serviceaccount.yaml
kind: ServiceAccount
apiVersion: v1
metadata:
  name: octavia-ingress-controller
  namespace: kube-system
---
kind: ClusterRoleBinding
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: octavia-ingress-controller
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: cluster-admin
subjects:
  - kind: ServiceAccount
    name: octavia-ingress-controller
    namespace: kube-system
EOF
kubectl apply -f /etc/kubernetes/octavia-ingress-controller/serviceaccount.yaml

cat <<EOF > /etc/kubernetes/octavia-ingress-controller/config.yaml
kind: ConfigMap
apiVersion: v1
metadata:
  name: octavia-ingress-controller-config
  namespace: kube-system
data:
  config: |
    cluster-name: kubernetes
    openstack:
      auth-url: https://auth.cloud.ovh.net/
      region: GRA9
      domain-id: default
      username: user-B56UekwqkPPw
      password: UnkskqK2JYg7maBR33VDTZ2vMr56BM5w
      project-id: edda3a79d3b34f789d8562be84fdc1aa
    octavia:
      floating-network-id: d7eaf2f8-d9d8-465b-9244-fd4736660570
      subnet-id: 6d1f6fbd-e5da-4551-af68-b16ef4826426
      provider: amphora
EOF
kubectl apply -f /etc/kubernetes/octavia-ingress-controller/config.yaml

cat <<EOF > /etc/kubernetes/octavia-ingress-controller/deployment.yaml
kind: StatefulSet
apiVersion: apps/v1
metadata:
  name: octavia-ingress-controller
  namespace: kube-system
  labels:
    k8s-app: octavia-ingress-controller
spec:
  replicas: 1
  selector:
    matchLabels:
      k8s-app: octavia-ingress-controller
  serviceName: octavia-ingress-controller
  template:
    metadata:
      labels:
        k8s-app: octavia-ingress-controller
    spec:
      serviceAccountName: octavia-ingress-controller
      tolerations:
        - effect: NoSchedule # Make sure the pod can be scheduled on master kubelet.
          operator: Exists
        - key: CriticalAddonsOnly # Mark the pod as a critical add-on for rescheduling.
          operator: Exists
        - effect: NoExecute
          operator: Exists
      containers:
        - name: octavia-ingress-controller
          image: docker.io/k8scloudprovider/octavia-ingress-controller:latest
          imagePullPolicy: IfNotPresent
          args:
            - /bin/octavia-ingress-controller
            - --config=/etc/config/octavia-ingress-controller-config.yaml
          volumeMounts:
            - mountPath: /etc/kubernetes
              name: kubernetes-config
              readOnly: true
            - name: ingress-config
              mountPath: /etc/config
      hostNetwork: true
      volumes:
        - name: kubernetes-config
          hostPath:
            path: /etc/kubernetes
            type: Directory
        - name: ingress-config
          configMap:
            name: octavia-ingress-controller-config
            items:
              - key: config
                path: octavia-ingress-controller-config.yaml
EOF
kubectl apply -f /etc/kubernetes/octavia-ingress-controller/deployment.yaml
kubectl logs octavia-ingress-controller-0 -n kube-system -f
