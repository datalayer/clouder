variable "ovh_env" {
  default = {
    project_id   = "f44f72e522ef47199095e7a67467304f"
    region       = "GRA9"
    service_name = "pn-1089500"
    vlan_id      = 169
  }
}

variable "instances" {
  default = {
    count         = 4
    flavor        = "c2-15"
    image_name    = "Centos 7" # "Centos 7" | "Debian 10" | "Ubuntu 22.04"
    username      = "centos" # "centos" | "debian" | "ubuntu"
    common_script = "kubeadm-common_centos_7.sh" # "kubeadm-common_centos_7.sh" | kubeadm-common_ubuntu_20.04.sh
  }
}

resource "openstack_compute_keypair_v2" "dla_keypair" {
  provider   = openstack.ovh
  name       = "dla-keypair"
  public_key = file("~/.ssh/id_rsa.pub")
}
/*
# Associate cloud project to vRack if not yet done.
resource "ovh_vrack_cloudproject" "vcp" {
  provider     = ovh.ovh # Provider name declared in provider.tf
  service_name = var.ovh_env.service_name
  project_id   = var.ovh_env.project_id
}
*/
resource "ovh_cloud_project_network_private" "dla_network" {
  service_name = var.ovh_env.project_id
  name         = "dla-network"
  regions      = [var.ovh_env.region]
  vlan_id      = var.ovh_env.vlan_id
  provider     = ovh.ovh
#  depends_on   = [ovh_vrack_cloudproject.vcp]
}
/*
resource "openstack_networking_network_v2" "dla_network" {
  name           = "dla-network"
  admin_state_up = true
  external       = true
}
*/
resource "openstack_networking_router_v2" "dla_router" {
  name                = "dla-router"
  admin_state_up      = true
  external_network_id = "b2c02fdc-ffdf-40f6-9722-533bd7058c06"
}

resource "ovh_cloud_project_network_private_subnet" "dla_subnet" {
  service_name = var.ovh_env.project_id
  network_id   = ovh_cloud_project_network_private.dla_network.id
  start        = "192.168.168.100"
  end          = "192.168.168.200"
  network      = "192.168.168.0/24"
  dhcp         = true
  no_gateway   = false
#  gateway_ip   = "192.168.168.1" # The gateway IP can not be assigned via terraform.
  region       = var.ovh_env.region
  provider     = ovh.ovh
  depends_on   = [ ovh_cloud_project_network_private.dla_network ]
}
/*
resource "openstack_networking_subnet_v2" "dla_subnet" {
  network_id   = ovh_cloud_project_network_private.dla_network.id
  cidr         = "192.168.199.0/24"
  enable_dhcp  = true
  no_gateway   = false
  dns_nameservers = [ "8.8.8.8" ] # 1.1.1.1, 8.8.8.8, 4.4.4.4
#  gateway_ip   = "192.168.168.1" # The gateway IP can not be assigned via terraform.
  region       = var.ovh_env.region
  depends_on   = [ovh_cloud_project_network_private.dla_network]
}
*/
resource "openstack_networking_router_interface_v2" "dla_router_interface" {
  router_id = "${openstack_networking_router_v2.dla_router.id}"
  subnet_id = "${ovh_cloud_project_network_private_subnet.dla_subnet.id}"
}

resource "openstack_compute_instance_v2" "dla_node" {
  name         = "dla-node-${count.index+1}"
  count        = var.instances.count
  provider     = openstack.ovh
  image_name   = var.instances.image_name
  flavor_name  = var.instances.flavor
  key_pair     = openstack_compute_keypair_v2.dla_keypair.name
  network {
      name     = ovh_cloud_project_network_private.dla_network.name
  }
  depends_on   = [ ovh_cloud_project_network_private_subnet.dla_subnet ]
}

resource "openstack_networking_floatingip_v2" "floating_ip" {
  count = var.instances.count
  pool  = "Ext-Net"
}

resource "openstack_compute_floatingip_associate_v2" "dla_associate" {
  count = var.instances.count
  floating_ip = openstack_networking_floatingip_v2.floating_ip[count.index].address
  instance_id = openstack_compute_instance_v2.dla_node[count.index].id
  depends_on = [
    openstack_compute_instance_v2.dla_node,
    openstack_networking_router_interface_v2.dla_router_interface,
  ]
}

resource "null_resource" "dla_setup_nodes" {
  count = var.instances.count
  provisioner "file" {
    source      = format("./../_resources/%s", var.instances.common_script)
    destination = format("/tmp/%s", var.instances.common_script)
    connection {
       host        = openstack_networking_floatingip_v2.floating_ip[count.index].address
       type        = "ssh"
       user        = var.instances.username
       agent       = "false"
       private_key = "${file("~/.ssh/id_rsa")}"
    }
  }
  provisioner "file" {
    source      = "./../_resources/openstack-api-ca.pem"
    destination = "/tmp/openstack-api-ca.cert"
    connection {
       host        = openstack_networking_floatingip_v2.floating_ip[count.index].address
       type        = "ssh"
       user        = var.instances.username
       agent       = "false"
       private_key = "${file("~/.ssh/id_rsa")}"
    }
  }
  provisioner "file" {
    source      = "./../_resources/cloud.conf"
    destination = "/tmp/cloud.conf"
    connection {
       host        = openstack_networking_floatingip_v2.floating_ip[count.index].address
       type        = "ssh"
       user        = var.instances.username
       agent       = "false"
       private_key = "${file("~/.ssh/id_rsa")}"
    }
  }
  provisioner "file" {
    source      = "./../_resources/kubeadm-master.yaml"
    destination = "/tmp/kubeadm-master.yaml"
    connection {
       host        = openstack_networking_floatingip_v2.floating_ip[count.index].address
       type        = "ssh"
       user        = var.instances.username
       agent       = "false"
       private_key = "${file("~/.ssh/id_rsa")}"
    }
  }
  provisioner "remote-exec" {
    connection {
       host        = openstack_networking_floatingip_v2.floating_ip[count.index].address
       type        = "ssh"
       user        = var.instances.username
       agent       = "false"
       private_key = "${file("~/.ssh/id_rsa")}"
    }
    inline = [
      format("chmod +x /tmp/%s", var.instances.common_script),
      format("sudo /tmp/%s > /tmp/out.txt", var.instances.common_script)
    ]
  }
  /*
  provisioner "local-exec" {
    command = "scp -i ./demo-key -o StrictHostKeyChecking=no  -o UserKnownHostsFile=/dev/null ubuntu@${openstack_compute_instance_v2.kubecluster_master.0.access_ip_v4}:/home/ubuntu/token ."
  }
  provisioner "file" {
    source      = "./token"
    destination = "/home/ubuntu/token"
    connection {
       host = "${openstack_compute_instance_v2.kubecluster_workers.0.access_ip_v4}"
       type = "ssh"
       user = "ubuntu"
       agent = "false"
       private_key = "${file("./demo-key")}"
    }
  }
  */
  depends_on   = [ openstack_compute_floatingip_associate_v2.dla_associate ]
}
