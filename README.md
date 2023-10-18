[![Datalayer](https://assets.datalayer.tech/datalayer-25.svg)](https://datalayer.io)

[![Become a Sponsor](https://img.shields.io/static/v1?label=Become%20a%20Sponsor&message=%E2%9D%A4&logo=GitHub&style=flat&color=1ABC9C)](https://github.com/sponsors/datalayer)

# ☁️ Clouder

> Create, manage and share Kubernetes clusters.

Clouder is a JupyterLab extension to interact with cloud services.

Devops can manage SSH Keys, virtual machines and Kubernetes clusters.

OVHcloud is supported for now. More cloud support is planned in subsequent Datalayer releases.

Clouder aims to:

- Create and monitor Kubernetes clusters.
- Manage Helm deployments.
- Schedule the size of the Kubernetes clusters.
- Share the cluster and give controlled access to other users.
- Take backup and restore for disaster recovery.

## Install

```bash
pip install clouder
```

```bash
git clone https://github.com/datalayer/clouder && \
  cd clouder && \
  pip install -e .
```

## Start

```bash
clouder -h
clouder about
```

```bash
clouder setup
clouder status
```

```bash
# Start the Clouder operator.
clouder operator start
```

## Use

```bash
# Get your profile details.
clouder profile
```

```bash
# Manage Projects.
clouder projects list
```

```bash
# Manage Keys.
clouder keys create
clouder keys list
clouder keys delete
```

```bash
# Manage Virtual Machines.
clouder vm list
```

```bash
# Manage Kubernetes.
clouder k8s list
```

```bash
# Start the Clouder server.
clouder server
```

## Stop

```bash
# Stop the Clouder operator.
clouder operator stop
```

## Develop

```bash
pip install -e .[test]
jupyter labextension develop . --overwrite
jupyter labextension list
jupyter server extension list
# open http://localhost:8888/lab?token=60c1661cc408f978c309d04157af55c9588ff9557c9380e4fb50785750703da6
yarn jupyterlab
```

## Releases

Clouder is released in [PyPI](https://pypi.org/project/clouder).
