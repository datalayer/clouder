/*****************************************************************************
 MASTER NODES
******************************************************************************/

output dla-master-floating-ip {
   value = "${openstack_networking_floatingip_v2.dla_master_floatingips.*.address}"
}

output dla-master-nodes-access-ip {
   value = "${openstack_compute_instance_v2.dla_master_nodes.*.access_ip_v4}"
}
/*
output dla-master-nodes-net-0-fixed-ip {
   value = "${openstack_compute_instance_v2.dla_master_nodes.*.network.0.fixed_ip_v4}"
}
*/
/*****************************************************************************
 WORKER NODES
******************************************************************************/

output dla-worker-floating-ip {
   value = "${openstack_networking_floatingip_v2.dla_worker_floatingips.*.address}"
}

output dla-worker-nodes-access-ip {
   value = "${openstack_compute_instance_v2.dla_worker_nodes.*.access_ip_v4}"
}
/*
output dla-worker-nodes-net-0-fixed-ip {
   value = "${openstack_compute_instance_v2.dla_worker_nodes.*.network.0.fixed_ip_v4}"
}
*/
/*****************************************************************************
 NETWORK
******************************************************************************/

output private-subnet-id {
   value = "${ovh_cloud_project_network_private_subnet.dla_subnet.id}"
}
