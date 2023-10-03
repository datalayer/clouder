import ovh
import kopf
import kubernetes
import pykube
from sshkey_tools.keys import RsaPrivateKey

from ...cloud.ovh.api import create_ovh_ssh_key, delete_ovh_ssh_key, get_ovh_ssh_key

config = None


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


@kopf.timer('sshkeys', interval=60, idle=100)
def create_sshkey_handler(status, logger, **_):
    info = status['create_sshkey_handler']
    project = info['project']
    id = info['id']
    try:
        get_ovh_ssh_key(project, id)
    except ovh.exceptions.ResourceNotFoundError:
        message = "SSH Key id " + id + " is not found."
        logger.warn(message)
        return {
            "message": message
        }


@kopf.on.create('sshkeys')
def create_sshkey_handler(spec, meta, logger, memo: kopf.Memo, **kwargs):
    logger.info(memo.create_tpl.format(**kwargs))
    logger.info(f"Creating SSH Key {meta['name']}")
    rsa_priv = RsaPrivateKey.generate()
    rsa_pub = rsa_priv.public_key
    public_key = rsa_pub.to_string("utf-8")
    project = spec["project"]
    res = create_ovh_ssh_key(
        project,
        meta['name'],
        public_key,
    )
    id = res["id"]
    finger_print = res["fingerPrint"]
    return {
        "id": id,
        "publicKey": public_key,
        "fingerPrint": finger_print,
        "name": res["name"],
        "regions": ",".join(res["regions"]),
        "project": project,
        "message": "SSH Key is available [id: " + id + ", fingerprint: " + finger_print + "]",
    }


@kopf.on.delete('sshkeys')
def delete_sshkey_handler(logger, status, memo: kopf.Memo, **kwargs):
    logger.info(status)
    info = status['create_sshkey_handler']
    logger.info(memo.delete_tpl.format(**kwargs))
    try:
        delete_ovh_ssh_key(
            info["project"],
            info["id"],
        )
        logger.info(f"SSH Key {info['name']} is deleted")
    except ovh.exceptions.ResourceNotFoundError:
        logger.warn(f"SSH Key {info['name']} is not found")
