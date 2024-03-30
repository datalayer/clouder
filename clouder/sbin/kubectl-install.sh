#!/usr/bin/env bash

# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

echo -e $BOLD$YELLOW"Installing Kubectl"$NOCOLOR$NOBOLD
echo

install_on_linux() {
#    curl -Lo /usr/local/bin/kubectl https://storage.googleapis.com/kubernetes-release/release/$(curl -s https://storage.googleapis.com/kubernetes-release/release/stable.txt)/bin/linux/amd64/kubectl && \
    curl -Lo /usr/local/bin/kubectl "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" && \
        chmod +x /usr/local/bin/kubectl
    echo
}

install_on_macos() {
    # curl -Lo /usr/local/bin/kubectl https://storage.googleapis.com/kubernetes-release/release/$(curl -s https://storage.googleapis.com/kubernetes-release/release/stable.txt)/bin/darwin/amd64/kubectl && \
    curl -Lo /usr/local/bin/kubectl "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/darwin/amd64/kubectl" && \
        chmod +x /usr/local/bin/kubectl
    echo
}

# https://github.com/kubernetes-sigs/krew
install_crew() {
(
  set -x; cd "$(mktemp -d)" &&
  OS="$(uname | tr '[:upper:]' '[:lower:]')" &&
  ARCH="$(uname -m | sed -e 's/x86_64/amd64/' -e 's/\(arm\)\(64\)\?.*/\1\2/' -e 's/aarch64$/arm64/')" &&
  curl -fsSLO "https://github.com/kubernetes-sigs/krew/releases/download/v0.4.4/krew-${OS}_${ARCH}.tar.gz" &&
  tar xvfz krew-${OS}_${ARCH}.tar.gz &&
  KREW=./krew-"${OS}_${ARCH}" &&
  "$KREW" install krew
  rm ./krew-*
    cat << 'EOF'
Add this to your PATH:

export PATH=\"${KREW_ROOT:-$HOME/.krew}/bin:$PATH\"

EOF
)
}

case "${OS}" in
    LINUX)     install_on_linux;;
    MACOS)     install_on_macos;;
    *)         echo "Unsupported operating system ${OS}"
esac

echo -e $BOLD$YELLOW"Installing Crew"$NOCOLOR$NOBOLD
echo
install_crew

CLOUDER_SKIP_HEADER=true clouder kubectl-help
