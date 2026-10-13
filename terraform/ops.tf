# Production-shaped layout.
# Ops holds the pipelines that actually deploy a tenant.
# ci-templates/pipelines holds the one file every tenant includes.

resource "gitlab_group" "ops" {
  name        = "Ops"
  path        = "Ops"
  description = "tenant_baseline, tenant_services, and Kasm_VDI."
}

resource "gitlab_group" "ci_templates" {
  name        = "ci-templates"
  path        = "ci-templates"
  description = "Shared CI included by every tenant repository."
}

resource "gitlab_project" "ci_template" {
  name                   = "pipelines"
  path                   = "pipelines"
  namespace_id           = gitlab_group.ci_templates.id
  description            = "One CI file: baseline, services, and Kasm, chosen by PIPELINE_TRIGGER."
  visibility_level       = "private"
  default_branch         = "main"
  initialize_with_readme = true
}

resource "gitlab_repository_file" "ci_template" {
  project        = gitlab_project.ci_template.id
  file_path      = "tenant.gitlab-ci.yml"
  branch         = "main"
  content        = file("${path.module}/pipeline/tenant.gitlab-ci.yml")
  encoding       = "text"
  author_email   = "terraform@localhost"
  author_name    = "Terraform"
  commit_message = "Add the shared tenant pipeline template"
}

# The orchestrator starts the tenant pipeline, and that pipeline includes this
# file. The tenants group is allowed too, so a tenant job can read it.
resource "gitlab_project_job_token_scope" "orchestrator_may_read_template" {
  project           = gitlab_project.ci_template.id
  target_project_id = gitlab_project.orchestrator.id
}

resource "gitlab_project_job_token_scope" "tenants_may_read_template" {
  project         = gitlab_project.ci_template.id
  target_group_id = gitlab_group.tenants.id
}

resource "gitlab_project" "ops" {
  for_each = {
    tenant_baseline = {
      name        = "tenant_baseline"
      description = "Terraform applied to one tenant using that tenant's tfvars."
      ci_file     = "ops-baseline.yml"
    }
    tenant_services = {
      name        = "tenant_services"
      description = "Service deploy for one tenant."
      ci_file     = "ops-services.yml"
    }
    Kasm_VDI = {
      name        = "Kasm_VDI"
      description = "Kasm VDI deploy for one tenant."
      ci_file     = "ops-kasm.yml"
    }
  }

  name                   = each.value.name
  path                   = each.key
  namespace_id           = gitlab_group.ops.id
  description            = each.value.description
  visibility_level       = "private"
  default_branch         = "main"
  initialize_with_readme = true
}

resource "gitlab_repository_file" "ops_pipeline" {
  for_each = gitlab_project.ops

  project        = each.value.id
  file_path      = ".gitlab-ci.yml"
  branch         = "main"
  content        = file("${path.module}/pipeline/${each.key == "tenant_baseline" ? "ops-baseline.yml" : each.key == "tenant_services" ? "ops-services.yml" : "ops-kasm.yml"}")
  encoding       = "text"
  author_email   = "terraform@localhost"
  author_name    = "Terraform"
  commit_message = "Add the ${each.key} pipeline"

  depends_on = [gitlab_project.ops]
}

resource "gitlab_repository_file" "baseline_terraform" {
  project        = gitlab_project.ops["tenant_baseline"].id
  file_path      = "main.tf"
  branch         = "main"
  content        = file("${path.module}/pipeline/baseline-main.tf")
  encoding       = "text"
  author_email   = "terraform@localhost"
  author_name    = "Terraform"
  commit_message = "Add the tenant baseline Terraform"
}

resource "gitlab_project_job_token_scope" "tenants_may_trigger_ops" {
  for_each = gitlab_project.ops

  project         = each.value.id
  target_group_id = gitlab_group.tenants.id
}

# Ops copies terraform.tfvars out of the tenant repo, so each tenant allows
# the Ops group to read it with a job token.
resource "gitlab_project_job_token_scope" "ops_may_read_tenant" {
  for_each = gitlab_project.tenant

  project         = each.value.id
  target_group_id = gitlab_group.ops.id
}

resource "gitlab_project_job_token_scope" "ops_may_read_car" {
  for_each = gitlab_project.car_mark

  project         = each.value.id
  target_group_id = gitlab_group.ops.id
}
