import ovh


client = ovh.Client()
applications = client.get('/me/api/application')


for application in applications:
    print(application)
