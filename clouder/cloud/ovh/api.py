"""
OVHcloud API.

https://api.ovh.com/console/
"""

import datetime

from tabulate import tabulate

import ovh


ovh_client = ovh.Client()


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


def get_ovh_me():
    """Get OVHcloud me."""
    return ovh_client.get('/me')


def get_ovh_projects():
    """Get the OVHcloud projects."""
    return ovh_client.get(f'/cloud/project')


def get_ovh_ssh_keys(project_name):
    """Get the SSH keys."""
    return ovh_client.get(f'/cloud/project/{project_name}/sshkey')


def get_ovh_applications():
    return ovh_client.get('/me/api/application')


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


def create_ovh_ssh_key(project, name, public_key):
    return ovh_client.post(f'/cloud/project/{project}/sshkey',
        name         = name,
        publicKey    = public_key,
    )


def delete_ovh_ssh_key(project, keyId):
    return ovh_client.delete(f'/cloud/project/{project}/sshkey/{keyId}')


def create_ovh_kubernetes(project):
    return ovh_client.post(f'/cloud/project/{project}/kube',
        name         = "kube-1",
        version      = "8",
        plan         = "essential",
        nodesList    = [
            {
                "flavor": "db1-7",
                "region": "BHS"
            }
        ]
    )


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
    print(tabulate(table, headers=['ID', 'App Name', 'Description',
                                'Token Creation', 'Token Expiration', 'Token Last Use']))


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


def get_ovh_bills():
    bills = ovh_client.get('/me/bill')
    for bill in bills:
        details = ovh_client.get('/me/bill/%s' % bill)
        print("%12s (%s): %10s --> %s" % (
            bill,
            details['date'],
            details['priceWithTax']['text'],
            details['pdfUrl'],
        ))
