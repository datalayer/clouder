# The account-level pieces of the Environments registry (PLAN_ENV.md, D-10, D-11, D-17, D-18).
#
# Environment artifacts are pushed to private ECR repositories under one prefix:
#   <prefix>/u/<owner_uid>/<environment>   user environments, created by the builder
#   <prefix>/platform/<environment>        platform environments, created by the builder
#   <prefix>/base/<channel>                base channels, created here
#   <prefix>/cache/u/<owner_uid>           BuildKit registry caches, created by the builder
#
# Nothing here holds a secret: the IAM users have no access keys in Terraform state.
# `clouder aws ecr-environments rotate-keys` creates and rotates them, and
# `k8s-ecr-environments-secrets.sh` puts them into Kubernetes Secrets.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name
  partition  = data.aws_partition.current.partition
  name       = "${var.project_name}-${var.repository_prefix}"
  registry   = "${local.account_id}.dkr.ecr.${local.region}.amazonaws.com"

  repositories_arn = "arn:${local.partition}:ecr:${local.region}:${local.account_id}:repository/${var.repository_prefix}/*"
  bases_arn        = "arn:${local.partition}:ecr:${local.region}:${local.account_id}:repository/${var.repository_prefix}/base/*"

  tags = merge(
    {
      "datalayer.io/component" = "environments-registry"
      "datalayer.io/project"   = var.project_name
    },
    var.tags,
  )
}

# --- KMS ---------------------------------------------------------------------------

resource "aws_kms_key" "encryption" {
  description             = "Encrypts the Environment images in ECR."
  deletion_window_in_days = var.kms_deletion_window_in_days
  enable_key_rotation     = true
  tags                    = local.tags
}

resource "aws_kms_alias" "encryption" {
  name          = "alias/${local.name}-ecr"
  target_key_id = aws_kms_key.encryption.key_id
}

# Asymmetric: cosign signs with it through `awskms:///alias/...`, and anyone holding
# kms:GetPublicKey verifies without ever holding the private half.
resource "aws_kms_key" "signing" {
  description              = "Signs Environment images once their scan passes."
  key_usage                = "SIGN_VERIFY"
  customer_master_key_spec = "ECC_NIST_P256"
  deletion_window_in_days  = var.kms_deletion_window_in_days
  tags                     = local.tags
}

resource "aws_kms_alias" "signing" {
  name          = "alias/${local.name}-signing"
  target_key_id = aws_kms_key.signing.key_id
}

# --- Repositories created here: the base channels ------------------------------------

resource "aws_ecr_repository" "base" {
  for_each = toset(var.base_channels)

  name                 = "${var.repository_prefix}/base/${each.value}"
  image_tag_mutability = "IMMUTABLE"

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.encryption.arn
  }

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = merge(local.tags, { Name = "${var.repository_prefix}/base/${each.value}" })
}

# --- Enhanced scanning ------------------------------------------------------------------

resource "aws_ecr_registry_scanning_configuration" "enhanced" {
  count     = var.manage_registry_scanning ? 1 : 0
  scan_type = "ENHANCED"

  rule {
    scan_frequency = var.scan_frequency

    repository_filter {
      filter      = "${var.repository_prefix}/*"
      filter_type = "WILDCARD"
    }

    dynamic "repository_filter" {
      for_each = var.extra_scan_filters
      content {
        filter      = repository_filter.value
        filter_type = "WILDCARD"
      }
    }
  }
}

# --- Principals ---------------------------------------------------------------------------

resource "aws_iam_user" "builder" {
  name = "${local.name}-builder"
  tags = local.tags
}

resource "aws_iam_user" "puller" {
  name = "${local.name}-puller"
  tags = local.tags
}

resource "aws_iam_user" "reader" {
  name = "${local.name}-reader"
  tags = local.tags
}

data "aws_iam_policy_document" "builder" {
  statement {
    sid       = "Login"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "EnvironmentRepositories"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchDeleteImage",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:CreateRepository",
      "ecr:DescribeImageScanFindings",
      "ecr:DescribeImages",
      "ecr:DescribeRepositories",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:ListImages",
      "ecr:PutImage",
      "ecr:PutLifecyclePolicy",
      "ecr:TagResource",
      "ecr:UploadLayerPart",
    ]
    resources = [local.repositories_arn]
  }

  statement {
    sid       = "EncryptRepositories"
    actions   = ["kms:CreateGrant", "kms:DescribeKey", "kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.encryption.arn]
  }

  statement {
    sid       = "Sign"
    actions   = ["kms:Sign", "kms:GetPublicKey", "kms:DescribeKey"]
    resources = [aws_kms_key.signing.arn]
  }

  statement {
    sid       = "HandBasesToAProviderBuild"
    actions   = ["sts:AssumeRole"]
    resources = [aws_iam_role.base_reader.arn]
  }
}

data "aws_iam_policy_document" "puller" {
  statement {
    sid       = "Login"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "PullEnvironments"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [local.repositories_arn]
  }

  statement {
    sid       = "DecryptLayers"
    actions   = ["kms:Decrypt", "kms:DescribeKey"]
    resources = [aws_kms_key.encryption.arn]
  }

  # The operator verifies a signature before it starts a pod (D-11).
  statement {
    sid       = "VerifySignatures"
    actions   = ["kms:GetPublicKey", "kms:DescribeKey"]
    resources = [aws_kms_key.signing.arn]
  }
}

data "aws_iam_policy_document" "reader" {
  statement {
    sid = "DescribeEnvironments"
    actions = [
      "ecr:DescribeImageScanFindings",
      "ecr:DescribeImages",
      "ecr:DescribeRepositories",
      "ecr:ListImages",
    ]
    resources = [local.repositories_arn]
  }

  # Enhanced scan findings are Amazon Inspector's.
  statement {
    sid       = "ReadFindings"
    actions   = ["inspector2:ListFindings", "inspector2:ListCoverage"]
    resources = ["*"]
  }
}

resource "aws_iam_user_policy" "builder" {
  name   = "${local.name}-builder"
  user   = aws_iam_user.builder.name
  policy = data.aws_iam_policy_document.builder.json
}

resource "aws_iam_user_policy" "puller" {
  name   = "${local.name}-puller"
  user   = aws_iam_user.puller.name
  policy = data.aws_iam_policy_document.puller.json
}

resource "aws_iam_user_policy" "reader" {
  name   = "${local.name}-reader"
  user   = aws_iam_user.reader.name
  policy = data.aws_iam_policy_document.reader.json
}

# --- The base-reader role: a managed-provider build's only credential (D-18) --------------

data "aws_iam_policy_document" "base_reader_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [aws_iam_user.builder.arn]
    }
  }
}

resource "aws_iam_role" "base_reader" {
  name                 = "${local.name}-base-reader"
  assume_role_policy   = data.aws_iam_policy_document.base_reader_trust.json
  max_session_duration = var.base_reader_session_seconds
  tags                 = local.tags
}

data "aws_iam_policy_document" "base_reader" {
  statement {
    sid       = "Login"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "PullBasesOnly"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [local.bases_arn]
  }

  statement {
    sid       = "DecryptBaseLayers"
    actions   = ["kms:Decrypt", "kms:DescribeKey"]
    resources = [aws_kms_key.encryption.arn]
  }
}

resource "aws_iam_role_policy" "base_reader" {
  name   = "${local.name}-base-reader"
  role   = aws_iam_role.base_reader.id
  policy = data.aws_iam_policy_document.base_reader.json
}
