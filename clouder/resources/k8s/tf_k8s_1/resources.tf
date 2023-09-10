/*****************************************************************************
 ENVIRONMENT
******************************************************************************/

variable "dla_env" {
  default = {
    project_id    = "a56bb1e272d5438aad6c84c5540906d8"
    region        = "BHS5"
    vrack_name    = "pn-1089500"
    vlan_id       = 170
    master_count  = 1
    master_flavor = "b2-15"    # "b2-15" "c2-15"
    worker_count  = 2
    worker_flavor = "t1-45"    # "t1-45" "t1-90"
    image_name    = "Centos 7" # "Centos 7" | "Debian 10" | "Ubuntu 22.04"
    username      = "centos"   # "centos" | "debian" | "ubuntu"
    common_script = "kubeadm-common_centos_7.sh" # "kubeadm-common_centos_7.sh" | kubeadm-common_ubuntu_20.04.sh
  }
}

/*****************************************************************************
 PROJECT
******************************************************************************/

/*****************************************************************************
 KEY PAIR
******************************************************************************/

resource "openstack_compute_keypair_v2" "dla_keypair" {
  provider   = openstack.ovh
  region     = var.dla_env.region
  name       = "dla-keypair"
  public_key = file("~/.ssh/id_rsa.pub")
}

/*****************************************************************************
 NETWORK
******************************************************************************/
/*
resource "ovh_vrack_cloudproject" "vcp" {
  provider     = ovh.ovh
  project_id   = var.dla_env.project_id
  service_name = var.dla_env.vrack_name
}

resource "openstack_networking_network_v2" "dla_network" {
  name           = "dla-network"
  admin_state_up = true
  external       = true
}

resource "openstack_networking_subnet_v2" "dla_subnet" {
  network_id   = ovh_cloud_project_network_private.dla_network.id
  cidr         = "192.168.199.0/24"
  enable_dhcp  = true
  no_gateway   = false
  dns_nameservers = [ "8.8.8.8" ] # 1.1.1.1, 8.8.8.8, 4.4.4.4
  region       = var.dla_env.region
  depends_on   = [ovh_cloud_project_network_private.dla_network]
}
*/
resource "ovh_cloud_project_network_private" "dla_network" {
  service_name = var.dla_env.project_id
  name         = "dla-network"
  regions      = [var.dla_env.region]
  vlan_id      = var.dla_env.vlan_id
  provider     = ovh.ovh
#  depends_on   = [ovh_vrack_cloudproject.vcp]
}

resource "openstack_networking_router_v2" "dla_router" {
  name                = "dla-router"
  region              = var.dla_env.region
  admin_state_up      = true
  external_network_id = "d7eaf2f8-d9d8-465b-9244-fd4736660570"
}

resource "ovh_cloud_project_network_private_subnet" "dla_subnet" {
  service_name = var.dla_env.project_id
  network_id   = ovh_cloud_project_network_private.dla_network.id
  start        = "192.168.168.100"
  end          = "192.168.168.200"
  network      = "192.168.168.0/24"
  dhcp         = true
  no_gateway   = false
  region       = var.dla_env.region
  provider     = ovh.ovh
  depends_on   = [ ovh_cloud_project_network_private.dla_network ]
}

resource "openstack_networking_router_interface_v2" "dla_router_interface" {
  router_id = "${openstack_networking_router_v2.dla_router.id}"
  subnet_id = "${ovh_cloud_project_network_private_subnet.dla_subnet.id}"
}

/*****************************************************************************
 MASTER NODES
******************************************************************************/

resource "openstack_compute_instance_v2" "dla_master_nodes" {
  name         = "dla-master-${count.index+1}"
  region       = var.dla_env.region
  count        = var.dla_env.master_count
  provider     = openstack.ovh
  image_name   = var.dla_env.image_name
  flavor_name  = var.dla_env.master_flavor
  key_pair     = openstack_compute_keypair_v2.dla_keypair.name
  network {
    name       = ovh_cloud_project_network_private.dla_network.name
  }
  depends_on   = [
    ovh_cloud_project_network_private_subnet.dla_subnet,
    openstack_networking_router_interface_v2.dla_router_interface
  ]
}

resource "openstack_networking_floatingip_v2" "dla_master_floatingips" {
  count   = var.dla_env.master_count
  region  = var.dla_env.region
  pool    = "Ext-Net"
}

resource "openstack_compute_floatingip_associate_v2" "dla_master_floatingips_associate" {
  count = var.dla_env.master_count
  floating_ip = openstack_networking_floatingip_v2.dla_master_floatingips[count.index].address
  instance_id = openstack_compute_instance_v2.dla_master_nodes[count.index].id
#  depends_on = [
#    openstack_compute_instance_v2.dla_master_nodes,
#    openstack_networking_router_interface_v2.dla_router_interface,
#  ]
}

resource "null_resource" "dla_setup_master_nodes" {
  count = var.dla_env.master_count
  provisioner "file" {
    source      = format("./../_resources/%s", var.dla_env.common_script)
    destination = format("/tmp/%s", var.dla_env.common_script)
    connection {
       host        = openstack_networking_floatingip_v2.dla_master_floatingips[count.index].address
       type        = "ssh"
       user        = var.dla_env.username
       agent       = "false"
       private_key = "${file("~/.ssh/id_rsa")}"
    }
  }
  provisioner "file" {
    source      = "./../_resources/openstack-api-ca.pem"
    destination = "/tmp/openstack-api-ca.cert"
    connection {
       host        = openstack_networking_floatingip_v2.dla_master_floatingips[count.index].address
       type        = "ssh"
       user        = var.dla_env.username
       agent       = "false"
       private_key = "${file("~/.ssh/id_rsa")}"
    }
  }
  provisioner "file" {
    source      = "./../_resources/cloud.conf"
    destination = "/tmp/cloud.conf"
    connection {
       host        = openstack_networking_floatingip_v2.dla_master_floatingips[count.index].address
       type        = "ssh"
       user        = var.dla_env.username
       agent       = "false"
       private_key = "${file("~/.ssh/id_rsa")}"
    }
  }
  provisioner "file" {
    source      = "./../_resources/kubeadm-master.yaml"
    destination = "/tmp/kubeadm-master.yaml"
    connection {
       host        = openstack_networking_floatingip_v2.dla_master_floatingips[count.index].address
       type        = "ssh"
       user        = var.dla_env.username
       agent       = "false"
       private_key = "${file("~/.ssh/id_rsa")}"
    }
  }
  provisioner "remote-exec" {
    connection {
       host        = openstack_networking_floatingip_v2.dla_master_floatingips[count.index].address
       type        = "ssh"
       user        = var.dla_env.username
       agent       = "false"
       private_key = "${file("~/.ssh/id_rsa")}"
    }
    inline = [
      format("chmod +x /tmp/%s", var.dla_env.common_script),
      format("sudo /tmp/%s > /tmp/out.txt", var.dla_env.common_script)
    ]
  }
  depends_on   = [ openstack_compute_floatingip_associate_v2.dla_master_floatingips_associate ]
}

/*****************************************************************************
 WORKER NODES
******************************************************************************/

resource "openstack_compute_instance_v2" "dla_worker_nodes" {
  name         = "dla-worker-${count.index+1}"
  region       = var.dla_env.region
  count        = var.dla_env.worker_count
  provider     = openstack.ovh
  image_name   = var.dla_env.image_name
  flavor_name  = var.dla_env.worker_flavor
  key_pair     = openstack_compute_keypair_v2.dla_keypair.name
  network {
    name       = ovh_cloud_project_network_private.dla_network.name
  }
  depends_on   = [
    ovh_cloud_project_network_private_subnet.dla_subnet,
    openstack_networking_router_interface_v2.dla_router_interface
  ]
}

resource "openstack_networking_floatingip_v2" "dla_worker_floatingips" {
  count   = var.dla_env.worker_count
  region  = var.dla_env.region
  pool    = "Ext-Net"
}

resource "openstack_compute_floatingip_associate_v2" "dla_worker_floatingips_associate" {
  count = var.dla_env.worker_count
  instance_id = openstack_compute_instance_v2.dla_worker_nodes[count.index].id
  floating_ip = openstack_networking_floatingip_v2.dla_worker_floatingips[count.index].address
#  depends_on = [
#    openstack_compute_instance_v2.dla_worker_nodes,
#    openstack_networking_router_interface_v2.dla_router_interface,
#  ]
}

resource "null_resource" "dla_setup_worker_nodes" {
  count = var.dla_env.worker_count
  provisioner "file" {
    source      = format("./../_resources/%s", var.dla_env.common_script)
    destination = format("/tmp/%s", var.dla_env.common_script)
    connection {
       host        = openstack_networking_floatingip_v2.dla_worker_floatingips[count.index].address
       type        = "ssh"
       user        = var.dla_env.username
       agent       = "false"
       private_key = "${file("~/.ssh/id_rsa")}"
    }
  }
  provisioner "file" {
    source      = "./../_resources/openstack-api-ca.pem"
    destination = "/tmp/openstack-api-ca.cert"
    connection {
       host        = openstack_networking_floatingip_v2.dla_worker_floatingips[count.index].address
       type        = "ssh"
       user        = var.dla_env.username
       agent       = "false"
       private_key = "${file("~/.ssh/id_rsa")}"
    }
  }
  provisioner "file" {
    source      = "./../_resources/cloud.conf"
    destination = "/tmp/cloud.conf"
    connection {
       host        = openstack_networking_floatingip_v2.dla_worker_floatingips[count.index].address
       type        = "ssh"
       user        = var.dla_env.username
       agent       = "false"
       private_key = "${file("~/.ssh/id_rsa")}"
    }
  }
  provisioner "file" {
    source      = "./../_resources/kubeadm-master.yaml"
    destination = "/tmp/kubeadm-master.yaml"
    connection {
       host        = openstack_networking_floatingip_v2.dla_worker_floatingips[count.index].address
       type        = "ssh"
       user        = var.dla_env.username
       agent       = "false"
       private_key = "${file("~/.ssh/id_rsa")}"
    }
  }
  provisioner "remote-exec" {
    connection {
       host        = openstack_networking_floatingip_v2.dla_worker_floatingips[count.index].address
       type        = "ssh"
       user        = var.dla_env.username
       agent       = "false"
       private_key = "${file("~/.ssh/id_rsa")}"
    }
    inline = [
      format("chmod +x /tmp/%s", var.dla_env.common_script),
      format("sudo /tmp/%s > /tmp/out.txt", var.dla_env.common_script)
    ]
  }
  depends_on   = [ openstack_compute_floatingip_associate_v2.dla_worker_floatingips_associate ]
}

/*****************************************************************************
 MSC
******************************************************************************/
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
