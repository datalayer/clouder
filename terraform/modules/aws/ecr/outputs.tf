output "repositories" {
  description = "Map of repository names to URL/ARN."
  value = {
    for name, repo in aws_ecr_repository.this :
    name => {
      arn = repo.arn
      url = repo.repository_url
    }
  }
}
