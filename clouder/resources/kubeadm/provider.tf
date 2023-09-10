terraform {
  required_version    = ">= 1.3.5"
  required_providers {
    openstack = {
      source  = "terraform-provider-openstack/openstack"
      version = "1.52.1"
    }
    ovh = {
      source  = "ovh/ovh"
      version = "0.31.0"
    }
  }
}

provider "openstack" {
  auth_url    = "https://auth.cloud.ovh.net/v3/" # Authentication URL
  domain_name = "default" # Domain name - Always at 'default' for OVHcloud
  alias       = "ovh" # An alias
}

provider "ovh" {
  alias              = "ovh"
}
