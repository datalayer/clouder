resource "aws_ecr_repository" "this" {
  for_each = toset(var.repository_names)

  name                 = each.value
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = var.scan_on_push
  }

  tags = {
    Name                     = each.value
    "datalayer.io/component" = "registry"
    "datalayer.io/project"   = var.project_name
  }
}

resource "aws_ecr_lifecycle_policy" "keep_last_50" {
  for_each = aws_ecr_repository.this

  repository = each.value.name
  policy     = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last 50 images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 50
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}
