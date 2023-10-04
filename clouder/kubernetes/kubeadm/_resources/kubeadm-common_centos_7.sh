#!/usr/bin/env sh

sleep 5

yum install -y yum-utils device-mapper-persistent-data lvm2

# Install containerd.
yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
yum -y update --exclude=kernel*,python-perf*,microcode*
yum -y install containerd

# https://kubernetes.io/docs/setup/production-environment/container-runtimes/#containerd
sudo containerd config default > /etc/containerd/config.toml
sudo sed -i 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml
# sudo sed -i 's#sandbox_image = "registry.k8s.io/pause:3.5"#sandbox_image = "registry.k8s.io/pause:3.2"#' /etc/containerd/config.toml
sudo systemctl restart containerd

# https://kubernetes.io/docs/setup/production-environment/container-runtimes/#install-and-configure-prerequisites
cat <<EOF | sudo tee /etc/modules-load.d/k8s.conf
overlay
br_netfilter
EOF
sudo modprobe overlay
sudo modprobe br_netfilter
lsmod | grep br_netfilter

# sysctl params required by setup, params persist across reboots.
cat <<EOF | sudo tee /etc/sysctl.d/k8s.conf
net.bridge.bridge-nf-call-iptables  = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward                 = 1
EOF
# Apply sysctl params without reboot
sudo sysctl --system

# 
cat <<EOF | sudo tee /etc/yum.repos.d/kubernetes.repo
[kubernetes]
name=Kubernetes
baseurl=https://packages.cloud.google.com/yum/repos/kubernetes-el7-\$basearch
enabled=1
gpgcheck=1
gpgkey=https://packages.cloud.google.com/yum/doc/rpm-package-key.gpg
exclude=kubelet kubeadm kubectl
EOF
# Set SELinux in permissive mode (effectively disabling it).
sudo setenforce 0
sudo sed -i 's/^SELINUX=enforcing$/SELINUX=permissive/' /etc/selinux/config
sudo yum install -y kubelet kubeadm kubectl --disableexcludes=kubernetes
sudo systemctl enable --now kubelet

# alias k='kubectl'

# kubeadm config images pull

export DATALAYER_HELM_VERSION=3.5.2
curl -Lo /tmp/helm-v${DATALAYER_HELM_VERSION}-linux-amd64.tar.gz https://get.helm.sh/helm-v${DATALAYER_HELM_VERSION}-linux-amd64.tar.gz \
  && tar xvfz /tmp/helm-v${DATALAYER_HELM_VERSION}-linux-amd64.tar.gz -C /tmp \
  && sudo mv /tmp/linux-amd64/helm /usr/local/bin \
  && sudo chmod +x /usr/local/bin/helm

# helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
# helm repo update
# helm -n ingress-nginx install ingress-nginx ingress-nginx/ingress-nginx --create-namespace

# helm repo add cpo https://kubernetes.github.io/cloud-provider-openstack
# helm repo update
# helm install openstack-ccm cpo/openstack-cloud-controller-manager --values openstack-ccm.yaml

mv /tmp/openstack-api-ca.cert /etc/ssl/certs/openstack-api-ca.cert

##########################
# OpenStack Cloud Provider
##########################

mkdir /etc/config || true
mv /tmp/cloud.conf /etc/config/cloud.conf
mkdir /etc/kubernetes || true
cp /etc/config/cloud.conf /etc/kubernetes/cloud-config

##########################
# OpenStack Cinder CSI
##########################

mkdir /etc/kubernetes/cinder-csi || true

cat <<EOF > /etc/kubernetes/cinder-csi/cinder-csi-nodeplugin.yaml
# This YAML file contains driver-registrar & csi driver nodeplugin API objects,
# which are necessary to run csi nodeplugin for cinder.
kind: DaemonSet
apiVersion: apps/v1
metadata:
  name: csi-cinder-nodeplugin
  namespace: kube-system
spec:
  selector:
    matchLabels:
      app: csi-cinder-nodeplugin
  template:
    metadata:
      labels:
        app: csi-cinder-nodeplugin
    spec:
      tolerations:
        - operator: Exists
      serviceAccount: csi-cinder-node-sa
      hostNetwork: true
      containers:
        - name: node-driver-registrar
          image: registry.k8s.io/sig-storage/csi-node-driver-registrar:v2.5.1
          args:
            - "--csi-address=\$(ADDRESS)"
            - "--kubelet-registration-path=\$(DRIVER_REG_SOCK_PATH)"
          env:
            - name: ADDRESS
              value: /csi/csi.sock
            - name: DRIVER_REG_SOCK_PATH
              value: /var/lib/kubelet/plugins/cinder.csi.openstack.org/csi.sock
            - name: KUBE_NODE_NAME
              valueFrom:
                fieldRef:
                  fieldPath: spec.nodeName
          imagePullPolicy: "IfNotPresent"
          volumeMounts:
            - name: socket-dir
              mountPath: /csi
            - name: registration-dir
              mountPath: /registration
        - name: liveness-probe
          image: registry.k8s.io/sig-storage/livenessprobe:v2.7.0
          args:
            - --csi-address=/csi/csi.sock
          volumeMounts:
            - name: socket-dir
              mountPath: /csi
        - name: cinder-csi-plugin
          securityContext:
            privileged: true
            capabilities:
              add: ["SYS_ADMIN"]
            allowPrivilegeEscalation: true
          image: docker.io/k8scloudprovider/cinder-csi-plugin:latest
          args:
            - /bin/cinder-csi-plugin
            - "--endpoint=\$(CSI_ENDPOINT)"
            - "--cloud-config=\$(CLOUD_CONFIG)"
          env:
            - name: CSI_ENDPOINT
              value: unix://csi/csi.sock
            - name: CLOUD_CONFIG
              value: /etc/config/cloud.conf
          imagePullPolicy: "IfNotPresent"
          ports:
            - containerPort: 9808
              name: healthz
              protocol: TCP
          # The probe
          livenessProbe:
            failureThreshold: 5
            httpGet:
              path: /healthz
              port: healthz
            initialDelaySeconds: 10
            timeoutSeconds: 3
            periodSeconds: 10
          volumeMounts:
            - name: socket-dir
              mountPath: /csi
            - name: kubelet-dir
              mountPath: /var/lib/kubelet
              mountPropagation: "Bidirectional"
            - name: pods-probe-dir
              mountPath: /dev
              mountPropagation: "HostToContainer"
            - name: secret-cinderplugin
              mountPath: /etc/config
              readOnly: true
            - name: cacert
              mountPath: /etc/ssl/certs
              readOnly: true
      volumes:
        - name: socket-dir
          hostPath:
            path: /var/lib/kubelet/plugins/cinder.csi.openstack.org
            type: DirectoryOrCreate
        - name: registration-dir
          hostPath:
            path: /var/lib/kubelet/plugins_registry/
            type: Directory
        - name: kubelet-dir
          hostPath:
            path: /var/lib/kubelet
            type: Directory
        - name: pods-probe-dir
          hostPath:
            path: /dev
            type: Directory
        - name: secret-cinderplugin
          secret:
            secretName: cloud-config
        - name: cacert
          hostPath:
            path: /etc/ssl/certs
EOF

cat <<EOF > /etc/kubernetes/cinder-csi/cinder-csi-controllerplugin.yaml
# This YAML file contains CSI Controller Plugin Sidecars
# external-attacher, external-provisioner, external-snapshotter, external-resize, liveness-probe
kind: Deployment
apiVersion: apps/v1
metadata:
  name: csi-cinder-controllerplugin
  namespace: kube-system
spec:
  replicas: 1
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 0
      maxSurge: 1
  selector:
    matchLabels:
      app: csi-cinder-controllerplugin
  template:
    metadata:
      labels:
        app: csi-cinder-controllerplugin
    spec:
      serviceAccount: csi-cinder-controller-sa
      containers:
        - name: csi-attacher
          image: registry.k8s.io/sig-storage/csi-attacher:v3.4.0
          args:
            - "--csi-address=\$(ADDRESS)"
            - "--timeout=3m"
            - "--leader-election=true"
          env:
            - name: ADDRESS
              value: /var/lib/csi/sockets/pluginproxy/csi.sock
          imagePullPolicy: "IfNotPresent"
          volumeMounts:
            - name: socket-dir
              mountPath: /var/lib/csi/sockets/pluginproxy/
        - name: csi-provisioner
          image: registry.k8s.io/sig-storage/csi-provisioner:v3.1.0
          args:
            - "--csi-address=\$(ADDRESS)"
            - "--timeout=3m"
            - "--default-fstype=ext4"
            - "--feature-gates=Topology=true"
            - "--extra-create-metadata"
            - "--leader-election=true"
          env:
            - name: ADDRESS
              value: /var/lib/csi/sockets/pluginproxy/csi.sock
          imagePullPolicy: "IfNotPresent"
          volumeMounts:
            - name: socket-dir
              mountPath: /var/lib/csi/sockets/pluginproxy/
        - name: csi-snapshotter
          image: registry.k8s.io/sig-storage/csi-snapshotter:v6.0.1
          args:
            - "--csi-address=\$(ADDRESS)"
            - "--timeout=3m"
            - "--extra-create-metadata"
            - "--leader-election=true"
          env:
            - name: ADDRESS
              value: /var/lib/csi/sockets/pluginproxy/csi.sock
          imagePullPolicy: Always
          volumeMounts:
            - mountPath: /var/lib/csi/sockets/pluginproxy/
              name: socket-dir
        - name: csi-resizer
          image: registry.k8s.io/sig-storage/csi-resizer:v1.4.0
          args:
            - "--csi-address=\$(ADDRESS)"
            - "--timeout=3m"
            - "--handle-volume-inuse-error=false"
            - "--leader-election=true"
          env:
            - name: ADDRESS
              value: /var/lib/csi/sockets/pluginproxy/csi.sock
          imagePullPolicy: "IfNotPresent"
          volumeMounts:
            - name: socket-dir
              mountPath: /var/lib/csi/sockets/pluginproxy/
        - name: liveness-probe
          image: registry.k8s.io/sig-storage/livenessprobe:v2.7.0
          args:
            - "--csi-address=\$(ADDRESS)"
          env:
            - name: ADDRESS
              value: /var/lib/csi/sockets/pluginproxy/csi.sock
          volumeMounts:
            - mountPath: /var/lib/csi/sockets/pluginproxy/
              name: socket-dir
        - name: cinder-csi-plugin
          image: docker.io/k8scloudprovider/cinder-csi-plugin:latest
          args:
            - /bin/cinder-csi-plugin
            - "--endpoint=\$(CSI_ENDPOINT)"
            - "--cloud-config=\$(CLOUD_CONFIG)"
            - "--cluster=\$(CLUSTER_NAME)"
          env:
            - name: CSI_ENDPOINT
              value: unix://csi/csi.sock
            - name: CLOUD_CONFIG
              value: /etc/config/cloud.conf
            - name: CLUSTER_NAME
              value: kubernetes
          imagePullPolicy: "IfNotPresent"
          ports:
            - containerPort: 9808
              name: healthz
              protocol: TCP
          # The probe
          livenessProbe:
            failureThreshold: 5
            httpGet:
              path: /healthz
              port: healthz
            initialDelaySeconds: 10
            timeoutSeconds: 10
            periodSeconds: 60
          volumeMounts:
            - name: socket-dir
              mountPath: /csi
            - name: secret-cinderplugin
              mountPath: /etc/config
              readOnly: true
            - name: ca-certs
              mountPath: /etc/ssl/certs
              readOnly: true
      volumes:
      - name: socket-dir
        emptyDir:
      - name: secret-cinderplugin
        secret:
          secretName: cloud-config
      - hostPath:
          path: /etc/ssl/certs
        name: ca-certs
EOF

cat <<EOF > /etc/kubernetes/cinder-csi/csi-cinder-driver.yaml
apiVersion: storage.k8s.io/v1
kind: CSIDriver
metadata:
  name: cinder.csi.openstack.org
spec:
  attachRequired: true
  podInfoOnMount: true
  volumeLifecycleModes:
  - Persistent
  - Ephemeral
EOF

cat <<EOF > /etc/kubernetes/cinder-csi/csi-cinder-storage-class.yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: csi-sc-cinderplugin
  annotations:
    storageclass.kubernetes.io/is-default-class: "true"
provisioner: cinder.csi.openstack.org
EOF
