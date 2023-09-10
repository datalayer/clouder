def get_projects(client):
    return client.get(f'/cloud/project')
