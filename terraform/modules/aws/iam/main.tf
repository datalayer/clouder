data "aws_iam_policy_document" "ec2_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "kubeadm_nodes" {
  name               = "${var.project_name}-${var.cluster_name}-kubeadm-nodes"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume_role.json
}

resource "aws_iam_role_policy_attachment" "node_managed_policies" {
  for_each   = toset(var.node_managed_policy_arns)
  role       = aws_iam_role.kubeadm_nodes.name
  policy_arn = each.value
}

resource "aws_iam_instance_profile" "kubeadm_nodes" {
  name = "${var.project_name}-${var.cluster_name}-kubeadm-nodes"
  role = aws_iam_role.kubeadm_nodes.name
}