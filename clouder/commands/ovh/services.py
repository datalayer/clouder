import datetime

from tabulate import tabulate

import ovh


# Services type desired to mine. To speed up the script, delete service type you don't use!
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

client = ovh.Client()

services_will_expired = []

for service_type in service_types:
    try:
        service_list = client.get("/%s" % service_type)
        for service in service_list:
            service_infos = client.get("/%s/%s/serviceInfos" % (service_type, service))
            service_expiration_date = datetime.datetime.strptime(service_infos["expiration"], "%Y-%m-%d")
            services_will_expired.append([service_type, service, service_infos["status"], service_infos["expiration"]])
    except:
        pass

print(tabulate(services_will_expired, headers=["Type", "ID", "status", "expiration date"]))
