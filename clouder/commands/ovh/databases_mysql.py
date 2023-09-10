import os

import ovh


client = ovh.Client()

service_name = os.getenv("OVH_CLOUD_PROJECT_SERVICE")

mysqls = client.get(f'/cloud/project/{service_name}/database/mysql')

for mysql in mysqls:
    print('----', mysql)
    details = client.get(f'/cloud/project/{service_name}/database/mysql/{mysql}')
    print(details)
    print()
