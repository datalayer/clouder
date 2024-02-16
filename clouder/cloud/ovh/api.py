"""
OVHcloud API.
"""

import datetime
import ovh
from tabulate import tabulate

from ...util.utils import OVH_CONFIG_FILE

###

ovh_client = ovh.Client(
  config_file=OVH_CONFIG_FILE
)

###

SERVICE_TYPES = [
    "allDom",
    "cdn/dedicated",
    "cdn/website",
    "cdn/webstorage",
    "cloud/project",
    "cluster/hadoop",
    "dedicated/housing",
    "dedicated/nas",
    "dedicated/nasha",
    "dedicated/server",
    "dedicatedCloud",
    "domain/zone",
    "email/domain",
    "email/exchange",
    "freefax",
    "hosting/privateDatabase",
    "hosting/web",
    "hosting/windows",
    "hpcspot",
    "license/cloudLinux",
    "license/cpanel",
    "license/directadmin",
    "license/office",
    "license/plesk",
    "license/sqlserver",
    "license/virtuozzo",
    "license/windows",
    "license/worklight",
    "overTheBox",
    "pack/xdsl",
    "partner",
    "router",
    "sms",
    "telephony",
    "telephony/spare",
    "veeamCloudConnect",
    "vps",
    "xdsl",
    "xdsl/spare",
]

### Profile.

def get_ovh_me():
    """Get OVHcloud me."""
    return ovh_client.get('/me')

def get_ovh_credentials():
    credentials = ovh_client.get('/me/api/credential', status='validated')
    table = []
    for credential_id in credentials:
        credential_method = '/me/api/credential/' + str(credential_id)
        credential = ovh_client.get(credential_method)
        application = ovh_client.get(credential_method+'/application')
        table.append([
            credential_id,
            '[%s] %s' % (application['status'], application['name']),
            application['description'],
            credential['creation'],
            credential['expiration'],
            credential['lastUse'],
        ])
    print(tabulate(table, headers=['ID', 'App Name', 'Description', 'Token Creation', 'Token Expiration', 'Token Last Use']))

### Resources.

def get_ovh_bill_numbers():
    return ovh_client.get('/me/bill')

def get_ovh_bill(bill_number):
    return ovh_client.get(f"/me/bill/{bill_number}")

### SSH Key.

def get_ovh_ssh_key(project_id, key_id):
    return ovh_client.get(f'/cloud/project/{project_id}/sshkey/{key_id}')

def get_ovh_ssh_keys(project_id):
    return ovh_client.get(f'/cloud/project/{project_id}/sshkey')

def create_ovh_ssh_key(project_id, key_name, public_key):
    return ovh_client.post(f'/cloud/project/{project_id}/sshkey',
        name         = key_name,
        publicKey    = public_key,
    )

def delete_ovh_ssh_key(project_name, key_id):
    return ovh_client.delete(f'/cloud/project/{project_name}/sshkey/{key_id}')

### Kubernetes.

"""
        nodepool    = [
            {
                "desiredNodes": 1,
                "minNodes": 1,
                "maxNodes": 1,
                "name": "datalayer-router",
                "flavorName": "b2-7",
                "region": "BHS5",
            }
        ]
"""
def create_ovh_kubernetes(project_id, kubernetes_name):
    return ovh_client.post(f'/cloud/project/{project_id}/kube',
        name         = kubernetes_name,
        version      = "1.28",
        region       = "BHS5",
    )

def get_ovh_kubernetess(project_id):
    return ovh_client.get(f'/cloud/project/{project_id}/kube')

def get_ovh_kubernetes(project_id, kubernetes_id):
    return ovh_client.get(f'/cloud/project/{project_id}/kube/{kubernetes_id}')

def get_ovh_kubernetes_nodepools(project_id, kubernetes_id):
    return ovh_client.get(f'/cloud/project/{project_id}/kube/{kubernetes_id}/nodepool')

def get_ovh_kubernetes_nodepool(project_id, kubernetes_id, nodepool_id):
    return ovh_client.get(f'/cloud/project/{project_id}/kube/{kubernetes_id}/nodepool/{nodepool_id}')

def get_ovh_kubernetes_nodepool_nodes(project_id, kubernetes_id, nodepool_id):
    return ovh_client.get(f'/cloud/project/{project_id}/kube/{kubernetes_id}/nodepool/{nodepool_id}/nodes')

### Projects.

def get_ovh_projects():
    """Get the OVHcloud projects."""
    return ovh_client.get(f'/cloud/project')

def get_ovh_project(project_id):
    """Get the OVHcloud project."""
    return ovh_client.get(f'/cloud/project/{project_id}')

### Applications.

def get_ovh_applications():
    return ovh_client.get('/me/api/application')

### Services.

def get_ovh_services():
    services_will_expired = []
    for service_type in SERVICE_TYPES:
        try:
            service_list = ovh_client.get("/%s" % service_type)
            for service in service_list:
                service_infos = ovh_client.get("/%s/%s/serviceInfos" % (service_type, service))
                services_will_expired.append([service_type, service, service_infos["status"], service_infos["expiration"]])
        except:
            pass
    print(tabulate(services_will_expired, headers=["Type", "ID", "status", "expiration date"]))

def get_ovh_services_expiring():
    delay = 60
    delay_date = datetime.datetime.now() + datetime.timedelta(days=delay)
    services_will_expired = []
    for service_type in SERVICE_TYPES:
        service_list = ovh_client.get("/%s" % service_type)
        for service in service_list:
            service_infos = ovh_client.get("/%s/%s/serviceInfos" % (service_type, service))
            service_expiration_date = datetime.datetime.strptime(service_infos["expiration"], "%Y-%m-%d")
            if service_expiration_date < delay_date:
                services_will_expired.append([service_type, service, service_infos["status"], service_infos["expiration"]])
    print(tabulate(services_will_expired, headers=["Type", "ID", "status", "expiration date"]))

### Databases.

def get_ovh_mysql_database(project):
    return ovh_client.post(f'/cloud/project/{project}/database/mysql',
        description  = "mysql-1",
        version      = "8",
        plan         = "essential",
        nodesList    = [
            {
                "flavor": "db1-7",
                "region": "BHS"
            }
        ]
    )

def get_ovh_mysql_databases(project):
    mysqls = ovh_client.get(f'/cloud/project/{project}/database/mysql')
    for mysql in mysqls:
        details = ovh_client.get(f'/cloud/project/{project}/database/mysql/{mysql}')
        print(details)
