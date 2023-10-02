#!/usr/bin/env sh

# https://docs.nvidia.com/cuda/cuda-installation-guide-linux
# https://docs.nvidia.com/datacenter/tesla/tesla-installation-notes/index.html#centos7

###############################################################################

# https://www.server-world.info/en/note?os=CentOS_7&p=nvidia
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

###############################################################################

# conda
# https://docs.nvidia.com/cuda/cuda-installation-guide-linux/#conda-installation
# https://fmorenovr.medium.com/set-up-conda-environment-pytorch-1-7-cuda-11-1-96a8e93014cc
# https://towardsdatascience.com/managing-cuda-dependencies-with-conda-89c5d817e7e1

# https://www.nvidia.com/download/index.aspx
curl -o NVIDIA-Linux-x86_64-515.86.01.run https://us.download.nvidia.com/tesla/515.86.01/NVIDIA-Linux-x86_64-515.86.01.run
bash ./NVIDIA-Linux-x86_64-515.86.01.run

sudo yum-config-manager --add-repo https://developer.download.nvidia.com/compute/cuda/repos/rhel7/x86_64/cuda-rhel7.repo
sudo yum -y install git kernel-devel epel-release dkms opencl-headers
sudo yum -y install nvidia-driver-latest-dkms

nvidia-smi

curl -o miniconda-install.sh https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh && \
  chmod +x miniconda-install.sh && \
  ./miniconda-install.sh -b && \
  rm -I miniconda-install.sh
cat << 'EOF' >> ~/.bashrc
# >>> conda initialize >>>
__conda_setup="$('~/miniconda3/bin/conda' 'shell.bash' 'hook' 2> /dev/null)"
if [ $? -eq 0 ]; then
    eval "$__conda_setup"
else
    if [ -f "~/miniconda3/etc/profile.d/conda.sh" ]; then
        . "/~/miniconda3/etc/profile.d/conda.sh"
    else
        export PATH="~/miniconda3/bin:$PATH"
    fi
fi
unset __conda_setup
# <<< conda initialize <<<
EOF
sed -i "s|~|$HOME|g" ~/.bashrc
source ~/.bashrc
conda help
conda -V
conda deactivate && \
  conda remove -y --all -n gpu && \
  conda create -y -n gpu
conda activate gpu
# https://github.com/tensorflow/tensorflow/issues/52988
# https://stackoverflow.com/questions/55224016/importerror-libcublas-so-10-0-cannot-open-shared-object-file-no-such-file-or
#  tensorflow-gpu \
conda install -y python=3.11 pip \
  cuda cudatoolkit \
  pytorch pytorch-cuda torchvision torchaudio \
  -c nvidia -c pytorch
conda list -n gpu
mkdir -p /usr/local/cuda/bin
ln -s /root/miniconda3/envs/gpu/bin/nvcc /usr/local/cuda/bin/nvcc
# cat << 'EOF' >> ~/.bashrc
# export PATH=/usr/local/cuda/bin:$PATH
# export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH
# EOF
# source ~/.bashrc
nvidia-smi
nvcc --version

###############################################################################

# examples
yum -y install git
git clone https://github.com/nvidia/cuda-samples --depth 1
cd cuda-samples/Samples/1_Utilities/deviceQuery
make
./deviceQuery
cd ./../bandwidthTest
make
./bandwidthTest

###############################################################################

# pytorch

```py
import torch
torch.__version__
torch.cuda.is_available()
torch.zeros(1).cuda()
torch.cuda.device_count()
torch.version.cuda
current_device = torch.cuda.current_device()
torch.cuda.device(current_device)
torch.cuda.get_device_name(current_device)
torch.rand(5, 3)
```

###############################################################################

# tensorflow

```py
import tensorflow as tf
# 
print(tf.test.gpu_device_name())
tf.config.list_physical_devices('GPU')
print("Num GPUs Available: ", len(tf.config.experimental.list_physical_devices('GPU')))
```

###############################################################################

# numba

sudo conda -y install numba

numba -s

###############################################################################

# alternatives

# https://www.server-world.info/en/note?os=CentOS_7&p=cuda&f=5
# https://developer.nvidia.com/cuda-downloads

yum -y install git kernel-devel epel-release dkms opencl-headers

# option 0
# yum install https://dl.fedoraproject.org/pub/epel/epel-release-latest-7.noarch.rpm

# option 1
sudo yum-config-manager --add-repo https://developer.download.nvidia.com/compute/cuda/repos/rhel7/x86_64/cuda-rhel7.repo
sudo yum -y install nvidia-driver-latest-dkms cuda cuda-drivers

# option 2
# curl -o cuda-repo-rhel7-11-8-local-11.8.0_520.61.05-1.x86_64.rpm https://developer.download.nvidia.com/compute/cuda/11.8.0/local_installers/cuda-repo-rhel7-11-8-local-11.8.0_520.61.05-1.x86_64.rpm
# sudo rpm -i cuda-repo-rhel7-11-8-local-11.8.0_520.61.05-1.x86_64.rpm
# sudo yum update
# sudo yum -y install nvidia-driver-latest-dkms
# sudo yum -y install cuda

# option 3
# curl -o cuda_11.8.0_520.61.05_linux.run https://developer.download.nvidia.com/compute/cuda/11.8.0/local_installers/cuda_11.8.0_520.61.05_linux.run
# sudo sh cuda_11.8.0_520.61.05_linux.run

cat /usr/local/cuda/version.txt

#
nvidia-smi
cat << 'EOF' >> /etc/profile.d/cuda11.sh
export PATH=/usr/local/cuda-11.8/bin${PATH:+:${PATH}}
export LD_LIBRARY_PATH=/usr/local/cuda-11.8/lib64${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}
EOF
source /etc/profile.d/cuda11.sh
nvcc --version

# 
sudo yum install python3-pip
sudo rm -I /usr/bin/python
sudo ln -fs /usr/bin/python3 /usr/bin/python
sudo pip install torch torchvision torchaudio
