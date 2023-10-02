import kopf
import kubernetes
import pykube
from kubernetes import client
from sshkey_tools.keys import RsaPrivateKey

from ...cloud.ovh.api import create_ovh_ssh_key, delete_ovh_ssh_key

config = None

STEPS = 1


class SSHKey(pykube.objects.NamespacedAPIObject):
    version = "clouder.sh/v1"
    endpoint = "sshkeys"
    kind = "SSHKey"


@kopf.on.startup()
def auth_pykube_startup(**_):
    global config
    try:
        config = pykube.KubeConfig.from_service_account()  # cluster env vars
    except FileNotFoundError:
        config = pykube.KubeConfig.from_file()  # developer's config files


@kopf.on.startup()
def auth_client_startup(logger, **_):
    try:
        kubernetes.config.load_incluster_config()  # cluster env vars
        logger.info("configured in cluster with service account")
    except kubernetes.config.ConfigException as e1:
        try:
            kubernetes.config.load_kube_config()  # developer's config files
            logger.info("configured via kubeconfig file")
        except kubernetes.config.ConfigException as e2:
            raise Exception(f"Cannot authenticate neither in-cluster, nor via kubeconfig.")


@kopf.on.create('sshkeys')
def create_sskey_fn(spec, logger, memo: kopf.Memo, **kwargs):
    print(memo.create_tpl.format(**kwargs))
    logger.info(f"And here we are! Creating: {kwargs['name']}")
    rsa_priv = RsaPrivateKey.generate()
    rsa_pub = rsa_priv.public_key
    public_key = rsa_pub.to_string("utf-8")
    res = create_ovh_ssh_key(
        "a56bb1e272d5438aad6c84c5540906d8",
        kwargs['name'],
        public_key,
    )
    return {
        'id': res["id"],
        'publicKey': public_key,
        'fingerPrint': res["fingerPrint"],
        'name': res["name"],
        'regions': "".join(res["regions"]),
        'message': 'ssh key is created with id: ' + res["id"],
    }


@kopf.on.delete('sshkeys')
def delete_sshkey_fn(logger, memo: kopf.Memo, **kwargs):
    status = kwargs["status"]
    logger.info(status)
    res = delete_ovh_ssh_key(
        "a56bb1e272d5438aad6c84c5540906d8",
        status['create_sskey_fn']['id'],
    )
    logger.info(res)
    logger.info(memo.delete_tpl.format(**kwargs))


def create_sshkey(step):
    try:
        api = pykube.HTTPClient(pykube.KubeConfig.from_env())
        ssh_key = SSHKey(api, dict(
            apiVersion='clouder.sh/v1',
            kind='SSHKey',
            metadata=dict(
                namespace='default',
                name=f'sshkey-managed-{step}',
            ),
        ))
        ssh_key.create()
    except pykube.exceptions.HTTPError as exc:
        if exc.code in [409]:
            pass
        else:
            raise


def create_sshkeys():
    for step in range(STEPS):
        create_sshkey(step)


def delete_sshkey(step):
    try:
        api = pykube.HTTPClient(pykube.KubeConfig.from_env())
        ssh_key = SSHKey.objects(api, namespace='default').get_by_name(f'sshkey-managed-{step}')
        ssh_key.delete()
    except pykube.exceptions.HTTPError as exc:
        if exc.code in [404]:
            pass
        else:
            raise


def delete_sshkeys():
    for step in range(STEPS):
        delete_sshkey(step)


def get_sshkeys():
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
        plural="sshkeys",
    )
