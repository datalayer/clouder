import ovh


client = ovh.Client()

print("Welcome", client.get('/me')['firstname'])
print()
print("This is your details:", client.get('/me'))
