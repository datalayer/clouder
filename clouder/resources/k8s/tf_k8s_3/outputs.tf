##############
# Terraform outputs, we are returning here public ip to access your Master and your nodes or Minions.
# This is executed once all instances and associated remote_exec scripts are done
##############

output dla-nodes-access-ip {
   value = "${openstack_compute_instance_v2.dla_node.*.access_ip_v4}"
}

output dla-nodes-net-0-fixed-ip {
   value = "${openstack_compute_instance_v2.dla_node.*.network.0.fixed_ip_v4}"
}

output dla-floating-ip {
   value = "${openstack_networking_floatingip_v2.floating_ip.*.address}"
}
/*
output dla-nodes-net-1-fixed-ip {
   value = "${openstack_compute_instance_v2.dla_node.*.network.1.fixed_ip_v4}"
}
*/
output private-subnet-id {
   value = "${ovh_cloud_project_network_private_subnet.dla_subnet.id}"
}
