variable "tenant_name" {
  type = string
}

variable "tenant_path" {
  type = string
}

resource "terraform_data" "tenant" {
  input = var.tenant_path
}

output "deployed_tenant" {
  value = var.tenant_path
}
