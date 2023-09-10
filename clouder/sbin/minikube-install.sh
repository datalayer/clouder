#!/usr/bin/env bash

# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

echo -e $BOLD$YELLOW"Installing Minikube"$NOCOLOR$NOBOLD
echo

install_on_linux() {

    echo -e $YELLOW"Installing kvm2 driver"$NOCOLOR
    echo
    echo Ensure you have libvirt and qemu-kvm on your system...
    echo Debian/Ubuntu: sudo apt install libvirt-clients libvirt-daemon-system qemu-kvm
    echo Fedora/CentOS/RHEL: sudo yum install libvirt-daemon-kvm qemu-kvm
    echo

    echo -e $YELLOW"Add yourself to the libvirt group so you don't need to sudo"$NOCOLOR
    # NOTE: For older Debian/Ubuntu versions change the group to `libvirtd`
    sudo usermod -a -G libvirt $(whoami)
    echo Update your current session for the group change to take effect
    echo NOTE: For older Debian/Ubuntu versions change the group to libvirtd
    echo sudo newgrp libvirt && exit
    echo sudo chown -R $USER:libvirt /var/run/libvirt
    echo

    echo -e $YELLOW"Installing docker-machine-driver-kvm2"$NOCOLOR
    echo
    curl -LO https://storage.googleapis.com/minikube/releases/latest/docker-machine-driver-kvm2 \
      && sudo install docker-machine-driver-kvm2 /usr/local/bin \
      && rm docker-machine-driver-kvm2
    echo

    echo -e $YELLOW"Installing Minikube ${DATALAYER_MINIKUBE_VERSION} into /usr/local/bin"$NOCOLOR
    echo
    curl -Lo /usr/local/bin/minikube https://storage.googleapis.com/minikube/releases/v${DATALAYER_MINIKUBE_VERSION}/minikube-linux-amd64 \
        && chmod +x /usr/local/bin/minikube
    echo

}

install_on_macos() {

#    echo -e $YELLOW"Removing dhcpd_leases"$NOCOLOR
#    echo
#    sudo rm /var/db/dhcpd_leases
#    echo

    echo -e $YELLOW"Installing Hyperkit Driver"$NOCOLOR
    echo
#    git clone https://github.com/moby/hyperkit && \
#      cd hyperkit && \
#      make && \
#      cp ./build/hyperkit /usr/local/bin/hyperkit && \
#      chmod +x /usr/local/bin/hyperkit && \
#      cd .. && \
#      rm -fr hyperkit
    brew install hyperkit
    echo

    echo -e $YELLOW"Installing Minikube ${DATALAYER_MINIKUBE_VERSION} into /usr/local/bin"$NOCOLOR
    echo
    curl -Lo /usr/local/bin/minikube https://storage.googleapis.com/minikube/releases/v${DATALAYER_MINIKUBE_VERSION}/minikube-darwin-amd64 \
        && chmod +x /usr/local/bin/minikube
    echo

}

DATALAYER_SKIP_HEADER=true clouder minikube-rm || true

case "${OS}" in
    LINUX)     install_on_linux;;
    MACOS)     install_on_macos;;
    *)         echo "Unsupported operating system ${OS}"
esac

docker network create minikube

DATALAYER_SKIP_HEADER=true clouder minikube-help "$@"
