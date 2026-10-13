output "tenant_orchestrator" {
  description = "Project the portal triggers. Set GITLAB_PROJECT_ID to this id."
  value = {
    id  = gitlab_project.orchestrator.id
    url = gitlab_project.orchestrator.web_url
  }
}

output "car_mark_count" {
  description = "Number of CARs/<brand>/<mark> projects."
  value       = length(gitlab_project.car_mark)
}

output "ops_projects" {
  description = "Pipelines a tenant starts after the portal trigger arrives."
  value = {
    for name, project in gitlab_project.ops : name => project.web_url
  }
}

output "ci_template" {
  description = "Shared file included by every tenant .gitlab-ci.yml."
  value       = "${gitlab_project.ci_template.web_url}/-/blob/main/tenant.gitlab-ci.yml"
}

output "tenant_projects" {
  description = "Downstream projects started by the CI file tenant_orchestrator generates."
  value = {
    for name, project in gitlab_project.tenant : name => {
      id  = project.id
      url = project.web_url
    }
  }
}
