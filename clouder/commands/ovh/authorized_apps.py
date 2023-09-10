import ovh
from tabulate import tabulate

client = ovh.Client()

credentials = client.get('/me/api/credential', status='validated')

table = []
for credential_id in credentials:
    credential_method = '/me/api/credential/' + str(credential_id)
    credential = client.get(credential_method)
    application = client.get(credential_method+'/application')
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
