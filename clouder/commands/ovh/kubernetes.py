import os

import ovh


client = ovh.Client()

service_name = os.getenv("OVH_CLOUD_PROJECT_SERVICE")

k8s = client.post(f'/cloud/project/{service_name}/kube',
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

print(k8s)
