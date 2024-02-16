import kubernetes
import pykube
from kubernetes import client


class SSHKey(pykube.objects.NamespacedAPIObject):
    version = "clouder.sh/v1"
    endpoint = "ssh_keys"
    kind = "SSHKey"


def create_clouder_ssh_key(name):
    try:
        api = pykube.HTTPClient(pykube.KubeConfig.from_env())
        ssh_key = SSHKey(api, dict(
            apiVersion='clouder.sh/v1',
            kind='SSHKey',
            metadata=dict(
                namespace='default',
                name=name
            ),
            spec=dict(
                project='a56bb1e272d5438aad6c84c5540906d8',
                regions=[
                    "BSH",
                    "SGB",
                ]
            ),
        ))
        ssh_key.create()
    except pykube.exceptions.HTTPError as exc:
        if exc.code in [409]:
            pass
        else:
            raise


def delete_clouder_ssh_key(name):
    try:
        api = pykube.HTTPClient(pykube.KubeConfig.from_env())
        ssh_key = SSHKey.objects(api, namespace='default').get_by_name(name)
        ssh_key.delete()
    except pykube.exceptions.HTTPError as exc:
        if exc.code in [404]:
            pass
        else:
            raise


def get_clouder_ssh_keys():
    try:
        kubernetes.config.load_incluster_config()  # cluster env vars
    except kubernetes.config.ConfigException as e1:
        try:
            kubernetes.config.load_kube_config()  # developer's config files
        except kubernetes.config.ConfigException as e2:
            raise Exception(f"Cannot authenticate neither in-cluster, nor via kubeconfig.")
    api = client.CustomObjectsApi()
    return api.list_namespaced_custom_object(
        group="clouder.sh",
        version="v1",
        namespace="default",
        plural="ssh_keys",
    )
