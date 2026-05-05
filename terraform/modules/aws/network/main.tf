data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  selected_az                 = var.availability_zone != null ? var.availability_zone : data.aws_availability_zones.available.names[0]
  client_vpn_authorized_cidrs = length(var.client_vpn_authorized_cidrs) > 0 ? var.client_vpn_authorized_cidrs : [var.vpc_cidr]
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name                     = "${var.project_name}-vpc"
    "datalayer.io/cluster"  = var.cluster_name
    "datalayer.io/component" = "network"
  }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = {
    Name = "${var.project_name}-igw"
  }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.this.id
  cidr_block              = var.subnet_cidr
  availability_zone       = local.selected_az
  map_public_ip_on_launch = true

  tags = {
    Name                                 = "${var.project_name}-public"
    Tier                                 = "public"
    "kubernetes.io/cluster/${var.cluster_name}" = "shared"
    "kubernetes.io/role/elb"            = "1"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = {
    Name = "${var.project_name}-public-rt"
  }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "kubeadm" {
  name        = "${var.project_name}-kubeadm-sg"
  description = "Security group for Datalayer kubeadm control plane and worker nodes"
  vpc_id      = aws_vpc.this.id

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.allowed_ssh_cidrs
  }

  ingress {
    description = "Kubernetes API server"
    from_port   = 6443
    to_port     = 6443
    protocol    = "tcp"
    cidr_blocks = var.allowed_ssh_cidrs
  }

  ingress {
    description = "HTTP for ingress"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS for ingress"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "NodePort services"
    from_port   = 30000
    to_port     = 32767
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Kubelet, scheduler, controller, etcd"
    from_port   = 10250
    to_port     = 10259
    protocol    = "tcp"
    self        = true
  }

  ingress {
    description = "etcd peer and client"
    from_port   = 2379
    to_port     = 2380
    protocol    = "tcp"
    self        = true
  }

  ingress {
    description = "Flannel VXLAN"
    from_port   = 8472
    to_port     = 8472
    protocol    = "udp"
    self        = true
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project_name}-kubeadm-sg"
  }
}

resource "aws_security_group_rule" "client_vpn_ssh" {
  count             = var.enable_client_vpn ? 1 : 0
  type              = "ingress"
  from_port         = 22
  to_port           = 22
  protocol          = "tcp"
  security_group_id = aws_security_group.kubeadm.id
  cidr_blocks       = [var.client_vpn_client_cidr]
  description       = "SSH from Client VPN clients"
}

resource "aws_security_group_rule" "client_vpn_k8s_api" {
  count             = var.enable_client_vpn ? 1 : 0
  type              = "ingress"
  from_port         = 6443
  to_port           = 6443
  protocol          = "tcp"
  security_group_id = aws_security_group.kubeadm.id
  cidr_blocks       = [var.client_vpn_client_cidr]
  description       = "Kubernetes API from Client VPN clients"
}

resource "aws_security_group_rule" "client_vpn_nodeport" {
  count             = var.enable_client_vpn ? 1 : 0
  type              = "ingress"
  from_port         = 30000
  to_port           = 32767
  protocol          = "tcp"
  security_group_id = aws_security_group.kubeadm.id
  cidr_blocks       = [var.client_vpn_client_cidr]
  description       = "NodePort access from Client VPN clients"
}

resource "aws_ec2_client_vpn_endpoint" "this" {
  count                     = var.enable_client_vpn ? 1 : 0
  description               = "${var.project_name}-${var.cluster_name}-client-vpn"
  server_certificate_arn    = var.client_vpn_server_certificate_arn
  client_cidr_block         = var.client_vpn_client_cidr
  split_tunnel              = var.client_vpn_split_tunnel
  transport_protocol        = var.client_vpn_transport_protocol
  vpn_port                  = 443
  security_group_ids        = [aws_security_group.kubeadm.id]
  session_timeout_hours     = var.client_vpn_session_timeout_hours
  dns_servers               = var.client_vpn_dns_servers

  authentication_options {
    type                       = "certificate-authentication"
    root_certificate_chain_arn = var.client_vpn_client_root_certificate_chain_arn
  }

  connection_log_options {
    enabled = false
  }

  tags = {
    Name = "${var.project_name}-${var.cluster_name}-client-vpn"
  }
}

resource "aws_ec2_client_vpn_network_association" "this" {
  count                  = var.enable_client_vpn ? 1 : 0
  client_vpn_endpoint_id = aws_ec2_client_vpn_endpoint.this[0].id
  subnet_id              = aws_subnet.public.id
}

resource "aws_ec2_client_vpn_authorization_rule" "this" {
  for_each               = var.enable_client_vpn ? toset(local.client_vpn_authorized_cidrs) : toset([])
  client_vpn_endpoint_id = aws_ec2_client_vpn_endpoint.this[0].id
  target_network_cidr    = each.value
  authorize_all_groups   = true

  depends_on = [aws_ec2_client_vpn_network_association.this]
}

resource "aws_ec2_client_vpn_route" "this" {
  for_each               = var.enable_client_vpn ? toset(local.client_vpn_authorized_cidrs) : toset([])
  client_vpn_endpoint_id = aws_ec2_client_vpn_endpoint.this[0].id
  destination_cidr_block = each.value
  target_vpc_subnet_id   = aws_subnet.public.id

  depends_on = [aws_ec2_client_vpn_network_association.this]
}
