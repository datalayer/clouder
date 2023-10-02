import ovh


ovh_client = ovh.Client()


# Request RO, /me API access.
ck = ovh_client.new_consumer_key_request()

# ck.add_rules(ovh.API_READ_ONLY, "/me")
ck.add_recursive_rules(ovh.API_READ_WRITE, '/')

# Request a token.
validation = ck.request()

print("Please visit %s to authenticate" % validation['validationUrl'])
input("...and press Enter to continue...")

print("Welcome", ovh_client.get('/me')['firstname'])
print("Your 'consumer key' is '%s'" % validation['consumerKey'])
