data "aws_ami" "ubuntu_2204" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

locals {
  selected_ami = var.ami_id != null ? var.ami_id : data.aws_ami.ubuntu_2204.id
}

resource "aws_instance" "master" {
  count = var.master_count

  ami                         = local.selected_ami
  instance_type               = var.master_instance_type
  key_name                    = var.ssh_key_name
  subnet_id                   = var.subnet_id
  vpc_security_group_ids      = [var.security_group_id]
  associate_public_ip_address = true
  iam_instance_profile        = var.instance_profile_name

  root_block_device {
    volume_size           = var.root_volume_size_gb
    volume_type           = "gp3"
    delete_on_termination = true
  }

  tags = {
    Name                     = count.index == 0 ? "${var.cluster_name}-master" : "${var.cluster_name}-master-${count.index + 1}"
    "datalayer.io/cluster"  = var.cluster_name
    "datalayer.io/role"     = "master"
    "datalayer.io/component" = "kubeadm"
  }
}

resource "aws_instance" "worker" {
  count = var.worker_count

  ami                         = local.selected_ami
  instance_type               = var.worker_instance_type
  key_name                    = var.ssh_key_name
  subnet_id                   = var.subnet_id
  vpc_security_group_ids      = [var.security_group_id]
  associate_public_ip_address = true
  iam_instance_profile        = var.instance_profile_name

  root_block_device {
    volume_size           = var.root_volume_size_gb
    volume_type           = "gp3"
    delete_on_termination = true
  }

  tags = {
    Name                     = "${var.cluster_name}-node-${count.index + 1}"
    "datalayer.io/cluster"  = var.cluster_name
    "datalayer.io/role"     = "worker"
    "datalayer.io/component" = "kubeadm"
  }
}
