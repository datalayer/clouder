"""
You can also use the Python client to create the needed credentials so you application is authorized to access a customer account. To allow your application to access an account using the API on your behalf, you need a Consumer Key (CONSUMER_KEY).

Here is a sample code you can use to allow your application to access a customer's information.
"""

import ovh

from ...util.utils import OVH_CONFIG_FILE


ovh_client = ovh.Client()

# Request RO, /me API access.
ck = ovh_client.new_consumer_key_request()

# ck.add_rules(ovh.API_READ_ONLY, "/me")
# Note To request full and unlimited access to the API, you may use add_recursive_rules:
# Allow all GET, POST, PUT, DELETE on /* (full API)
ck.add_recursive_rules(ovh.API_READ_WRITE, '/')

# Request a token.
validation = ck.request()

print("Please visit %s to authenticate" % validation['validationUrl'])
input("...and press Enter to continue...")

# Print nice welcome message.

print("Welcome", ovh_client.get('/me')['firstname'])

# The returned `Consumer Key` should then be kept to avoid re-authenticating your end-user on each use.
print("Your 'consumer key' is '%s'" % validation['consumerKey'])
