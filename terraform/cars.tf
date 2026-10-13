# Nested tenants: tenants/CARs/<brand>/<mark>.
# Each mark is a project the orchestrator can trigger.

locals {
  car_marks = {
    Toyota    = ["camry", "corolla", "rav4", "highlander", "tacoma", "tundra", "prius"]
    Honda     = ["civic", "accord", "crv", "pilot", "odyssey", "ridgeline"]
    Hyundai   = ["kona", "elantra", "tucson", "santafe", "palisade", "ioniq"]
    Kia       = ["sportage", "telluride", "sorento", "forte", "soul", "ev6"]
    Ford      = ["f150", "mustang", "explorer", "escape", "bronco", "maverick"]
    Chevrolet = ["silverado", "equinox", "malibu", "tahoe", "traverse", "camaro"]
    Nissan    = ["altima", "rogue", "sentra", "pathfinder", "frontier", "murano"]
    BMW       = ["x3", "x5", "series3", "series5", "ix"]
    Audi      = ["a4", "q5", "q7", "a6"]
    Mercedes  = ["cclass", "eclass", "glc", "gle"]
  }

  car_projects = {
    for project in flatten([
      for brand, marks in local.car_marks : [
        for mark in marks : {
          key   = "${brand}/${mark}"
          brand = brand
          mark  = mark
        }
      ]
    ]) : project.key => project
  }
}

resource "gitlab_group" "cars" {
  name        = "CARs"
  path        = "CARs"
  parent_id   = gitlab_group.tenants.id
  description = "Car tenants. Each brand is a subgroup and each mark is a project."
}

resource "gitlab_group" "car_brand" {
  for_each = toset(keys(local.car_marks))

  name        = each.key
  path        = each.key
  parent_id   = gitlab_group.cars.id
  description = "${each.key} marks triggered by tenant_orchestrator."
}

resource "gitlab_project" "car_mark" {
  for_each = local.car_projects

  name                   = each.value.mark
  path                   = each.value.mark
  namespace_id           = gitlab_group.car_brand[each.value.brand].id
  description            = "Downstream pipeline for CARs/${each.value.brand}/${each.value.mark}."
  visibility_level       = "private"
  default_branch         = "main"
  initialize_with_readme = true
}

resource "gitlab_project_job_token_scope" "orchestrator_may_trigger_car" {
  for_each = gitlab_project.car_mark

  project           = each.value.id
  target_project_id = gitlab_project.orchestrator.id
}

resource "gitlab_repository_file" "car_pipeline" {
  for_each = gitlab_project.car_mark

  project        = each.value.id
  file_path      = ".gitlab-ci.yml"
  branch         = "main"
  content        = file("${path.module}/pipeline/tenant-entry.yml")
  encoding       = "text"
  author_email   = "terraform@localhost"
  author_name    = "Terraform"
  commit_message = "Point the tenant at the shared CI template"

  depends_on = [gitlab_project.car_mark, gitlab_repository_file.ci_template]
}

resource "gitlab_repository_file" "car_tfvars" {
  for_each = gitlab_project.car_mark

  project   = each.value.id
  file_path = "terraform.tfvars"
  branch    = "main"
  content = join("\n", [
    "tenant_name = \"${local.car_projects[each.key].mark}\"",
    "tenant_path = \"CARs/${each.key}\"",
    "",
  ])
  encoding       = "text"
  author_email   = "terraform@localhost"
  author_name    = "Terraform"
  commit_message = "Add CARs/${each.key} terraform.tfvars"

  depends_on = [gitlab_project.car_mark]
}
