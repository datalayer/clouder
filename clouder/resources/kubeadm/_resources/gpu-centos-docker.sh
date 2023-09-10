#!/usr/bin/env sh

# https://docs.nvidia.com/datacenter/cloud-native

###############################################################################
# docker
# https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html#installing-on-centos-7-8
# https://medium.com/@poom.wettayakorn/setting-up-nvidia-container-for-using-cuda-on-centos-7-db9d53c823e8
# https://wandb.ai/wandb_fc/tips/reports/Setting-Up-TensorFlow-And-PyTorch-Using-GPU-On-Docker--VmlldzoxNjU5Mzky

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
#
sudo yum-config-manager --add-repo=https://download.docker.com/linux/centos/docker-ce.repo
sudo yum repolist -v
sudo yum -y install docker-ce
sudo systemctl restart docker
sudo docker run --rm hello-world
# https://github.com/nvidia/nvidia-docker
distribution=$(. /etc/os-release;echo $ID$VERSION_ID) \
   && curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.repo | sudo tee /etc/yum.repos.d/nvidia-container-toolkit.repo
sudo yum clean expire-cache
sudo yum install -y nvidia-docker2
sudo systemctl restart docker
# https://hub.docker.com/r/nvidia/cuda/tags
sudo docker run --rm --gpus all nvidia/cuda:11.8.0-base-ubuntu20.04 nvidia-smi
