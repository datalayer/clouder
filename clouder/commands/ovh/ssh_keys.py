def get_keys(client, service_name):
    return client.get(f'/cloud/project/{service_name}/sshkey')
