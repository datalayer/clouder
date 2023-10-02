#!/usr/bin/env sh

sudo apt install gcc
sudo apt-get install linux-headers-$(uname -r)

wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-11-8_11.8.0-1_amd64.deb
sudo dpkg -i cuda-11-8_11.8.0-1_amd64.deb 

wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-ubuntu2204.pin
sudo mv cuda-ubuntu2204.pin /etc/apt/preferences.d/cuda-repository-pin-600

wget https://developer.download.nvidia.com/compute/cuda/11.8.0/local_installers/cuda-repo-ubuntu2204-11-8-local_11.8.0-520.61.05-1_amd64.deb
sudo dpkg -i cuda-repo-ubuntu2204-11-8-local_11.8.0-520.61.05-1_amd64.deb

sudo cp /var/cuda-repo-ubuntu2204-11-8-local/cuda-*-keyring.gpg /usr/share/keyrings/

sudo apt update
sudo apt-get -y install cuda
sudo apt --fix-broken install
sudo apt-get -y install cuda

sudo reboot

# sudo apt install python3-pip
# sudo apt install python3

# pip3 install torch torchvision torchaudio
