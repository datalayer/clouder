#!/usr/bin/env sh

# https://docs.nvidia.com/datacenter/cloud-native

###############################################################################
# kubernetes
# https://docs.nvidia.com/datacenter/cloud-native/kubernetes/install-k8s.html#option-2-installing-kubernetes-using-kubeadm
# https://docs.nvidia.com/datacenter/cloud-native/kubernetes/install-k8s.html#install-nvidia-dependencies
# https://github.com/nvidia/k8s-device-plugin
# https://levelup.gitconnected.com/running-gpu-enabled-containers-in-kubernetes-cluster-f0a3d87a450c

yum install -y yum-utils pciutils
lspci | grep -i nvidia
lsmod | grep nouveau
# add to the end (create new if it does not exist)
cat << EOF >>/etc/modprobe.d/blacklist-nouveau.conf
blacklist nouveau
options nouveau modeset=0
EOF
dracut --force
reboot
# 
yum -y install kernel-devel-$(uname -r) kernel-header-$(uname -r) gcc make
lspci | grep -i --color 'vga\|3d\|2d'
lshw -class display
sudo yum-config-manager --add-repo https://developer.download.nvidia.com/compute/cuda/repos/rhel7/x86_64/cuda-rhel7.repo
sudo yum -y install git kernel-devel epel-release dkms opencl-headers
sudo yum -y install nvidia-driver-latest-dkms
nvidia-smi

yum -y install containerd
distribution=$(. /etc/os-release;echo $ID$VERSION_ID) \
   && curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.repo | sudo tee /etc/yum.repos.d/nvidia-container-toolkit.repo
sudo yum install -y nvidia-container-runtime
sudo containerd config default > /etc/containerd/config.toml
```diff
--- config.toml 2020-12-17 19:13:03.242630735 +0000
+++ /etc/containerd/config.toml 2020-12-17 19:27:02.019027793 +0000
@@ -70,7 +70,7 @@
   ignore_image_defined_volumes = false
   [plugins."io.containerd.grpc.v1.cri".containerd]
      snapshotter = "overlayfs"
-      default_runtime_name = "runc"
+      default_runtime_name = "nvidia"
      no_pivot = false
      disable_snapshot_annotations = true
      discard_unpacked_layers = false
@@ -94,6 +94,15 @@
         privileged_without_host_devices = false
         base_runtime_spec = ""
         [plugins."io.containerd.grpc.v1.cri".containerd.runtimes.runc.options]
+            SystemdCgroup = true
+
+       [plugins."io.containerd.grpc.v1.cri".containerd.runtimes.nvidia]
+          privileged_without_host_devices = false
+          runtime_engine = ""
+          runtime_root = ""
+          runtime_type = "io.containerd.runc.v1"
+          [plugins."io.containerd.grpc.v1.cri".containerd.runtimes.nvidia.options]
+            BinaryName = "/usr/bin/nvidia-container-runtime"
+            SystemdCgroup = true
   [plugins."io.containerd.grpc.v1.cri".cni]
      bin_dir = "/opt/cni/bin"
      conf_dir = "/etc/cni/net.d"
```
sudo systemctl restart containerd

sudo ctr image pull docker.io/library/hello-world:latest
sudo ctr image ls
sudo ctr container create docker.io/library/hello-world:latest demo
sudo ctr container ls
sudo ctr container delete demo
sudo ctr images pull docker.io/nvidia/cuda:11.8.0-base-ubuntu20.04
sudo ctr run --rm --gpus 0 docker.io/nvidia/cuda:11.8.0-base-ubuntu20.04 nvidia-smi nvidia-smi

#
kubectl create -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.13.0/nvidia-device-plugin.yml
#
# helm repo add nvdp https://nvidia.github.io/k8s-device-plugin && helm repo update \
#   && helm install --generate-name nvdp/nvidia-device-plugin --namespace kube-system
#
kubectl get pods -A -w
kubectl get pods -A | grep nvidia-device-plugin

###############################################################################

# nvidia/samples
cat << EOF | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: gpu-test
spec:
  restartPolicy: OnFailure
  containers:
  - name: cuda-vector-add
    image: "nvidia/samples:vectoradd-cuda11.6.0-ubuntu18.04"
    resources:
      limits:
         nvidia.com/gpu: 1
EOF
kubectl get pods gpu-test
kubectl logs gpu-test
# [Vector addition of 50000 elements]
# Copy input data from the host memory to the CUDA device
# CUDA kernel launch with 196 blocks of 256 threads
# Copy output data from the CUDA device to the host memory
# Test PASSED
# Done
kubectl delete pod gpu-test

# gpu-sample
cat << EOF | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: gpu-sample
spec:
  restartPolicy: OnFailure
  containers:
  - name: gpu-sample
    image: "nvidia/cuda:11.8.0-devel-ubuntu22.04"
    command: [ "/bin/sh", "-c", "--" ]
    args: [ "while true; do sleep 300; done;" ]
    resources:
      limits:
         nvidia.com/gpu: 1
EOF
kubectl get pods gpu-sample
kubectl logs gpu-sample
kubectl exec -it gpu-sample -- bash
nvidia-smi
kubectl delete pod gpu-sample

# pytorch-gpu
cat << EOF | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: pytorch-gpu
spec:
  restartPolicy: OnFailure
  containers:
  - name: pytorch-gpu
    image: "nvcr.io/nvidia/pytorch:22.01-py3"
    command: [ "/bin/sh", "-c", "--" ]
    args: [ "while true; do sleep 300; done;" ]
    resources:
      limits:
         nvidia.com/gpu: 1
EOF
kubectl get pods pytorch-gpu
kubectl logs pytorch-gpu
kubectl exec -it pytorch-gpu -- bash
python
# import torch
# torch.cuda.is_available()
# True
kubectl delete pod pytorch-gpu
