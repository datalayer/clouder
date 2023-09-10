import datetime

from tabulate import tabulate

import ovh


service_types = [
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

delay = 60

client = ovh.Client()

delay_date = datetime.datetime.now() + datetime.timedelta(days=delay)

services_will_expired = []

for service_type in service_types:
    service_list = client.get("/%s" % service_type)
    for service in service_list:
        service_infos = client.get("/%s/%s/serviceInfos" % (service_type, service))
        service_expiration_date = datetime.datetime.strptime(service_infos["expiration"], "%Y-%m-%d")
        if service_expiration_date < delay_date:
            services_will_expired.append([service_type, service, service_infos["status"], service_infos["expiration"]])

print(tabulate(services_will_expired, headers=["Type", "ID", "status", "expiration date"]))
