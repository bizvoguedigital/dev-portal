# Two marks whose pipelines fail. skip-tenants.txt leaves them out of a full release.

resource "gitlab_group" "broken" {
  name        = "Broken"
  path        = "Broken"
  parent_id   = gitlab_group.cars.id
  description = "Tenants whose pipelines fail, used to exercise a partial release."
}

resource "gitlab_project" "broken_mark" {
  for_each = toset(["stall", "flat"])

  name                   = each.key
  path                   = each.key
  namespace_id           = gitlab_group.broken.id
  description            = "Fails on purpose. A full release skips CARs/Broken/${each.key}."
  visibility_level       = "private"
  default_branch         = "main"
  initialize_with_readme = true
}

resource "gitlab_project_job_token_scope" "orchestrator_may_trigger_broken" {
  for_each = gitlab_project.broken_mark

  project           = each.value.id
  target_project_id = gitlab_project.orchestrator.id
}

resource "gitlab_repository_file" "broken_pipeline" {
  for_each = gitlab_project.broken_mark

  project        = each.value.id
  file_path      = ".gitlab-ci.yml"
  branch         = "main"
  content        = file("${path.module}/pipeline/failing-ci.yml")
  encoding       = "text"
  author_email   = "terraform@localhost"
  author_name    = "Terraform"
  commit_message = "Add a pipeline that fails on purpose"

  depends_on = [gitlab_project.broken_mark]
}
