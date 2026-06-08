# Terraform — GCP infrastructure for FSI-RM
# Aligned with GoogleCloudPlatform/cloud-networking-solutions/demos/agent-gateway
#
# Run: terraform init && terraform apply -var project_id=<YOUR_GCP_PROJECT>

terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 6.0"
    }
  }
}

# ── Variables ─────────────────────────────────────────────────────────────────

variable "project_id"   { type = string }
variable "project_number" {
  type        = string
  description = "Numeric project number (used for Agent Identity principal)."
}
variable "organization_id" {
  type        = string
  description = "GCP org ID (used to build the agent identity principalSet URI)."
}
variable "region" {
  type    = string
  default = "us-east1"
}
variable "agent_engine_id" {
  type        = string
  default     = ""
  description = "Set after running deploy_agent.py; used to bind Agent Identity IAM roles."
}
variable "enable_agent_gateway" {
  type    = bool
  default = true
}
variable "enable_model_armor" {
  type    = bool
  default = true
}

# ── Providers ─────────────────────────────────────────────────────────────────

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}

# ── APIs ──────────────────────────────────────────────────────────────────────

resource "google_project_service" "apis" {
  for_each = toset([
    # Core
    "aiplatform.googleapis.com",
    "bigquery.googleapis.com",
    "run.googleapis.com",
    # Agent Platform
    "agentregistry.googleapis.com",
    "agentgateway.googleapis.com",
    "networkservices.googleapis.com",
    # Security
    "iap.googleapis.com",
    "modelarmor.googleapis.com",
    "iamcredentials.googleapis.com",   # required for SA impersonation
    # Observability
    "cloudtrace.googleapis.com",
    "monitoring.googleapis.com",
    "logging.googleapis.com",
    # Secrets
    "secretmanager.googleapis.com",
  ])
  service            = each.key
  disable_on_destroy = false
}

# ── BigQuery ──────────────────────────────────────────────────────────────────

resource "google_bigquery_dataset" "fsi_rm_poc" {
  dataset_id  = "fsi_rm_poc"
  location    = "us-east1"
  description = "FSI-RM PoC mock banking data"
  depends_on  = [google_project_service.apis]
}

# ── Per-MCP service accounts (least privilege, per-service isolation) ─────────
# Each MCP server gets its own SA. IAP enforcement happens at the Agent Gateway;
# Cloud Run only trusts calls that arrive via the Mcp Invoker SA (below).

locals {
  mcp_services = toset([
    "fsi-rm-core-banking-mcp",
    "fsi-rm-portfolio-mcp",
    "fsi-rm-comms-mcp",
    "fsi-rm-compliance-mcp",
    "fsi-rm-voice-mcp",
  ])
}

resource "google_service_account" "mcp" {
  for_each     = local.mcp_services
  account_id   = each.key
  display_name = "FSI-RM MCP SA — ${each.key}"
}

# All MCP SAs need BigQuery read access
resource "google_project_iam_member" "mcp_bq_reader" {
  for_each = local.mcp_services
  project  = var.project_id
  role     = "roles/bigquery.dataViewer"
  member   = "serviceAccount:${google_service_account.mcp[each.key].email}"
}

resource "google_project_iam_member" "mcp_bq_job" {
  for_each = local.mcp_services
  project  = var.project_id
  role     = "roles/bigquery.jobUser"
  member   = "serviceAccount:${google_service_account.mcp[each.key].email}"
}

# OTel / Cloud Trace for each MCP SA
resource "google_project_iam_member" "mcp_trace" {
  for_each = local.mcp_services
  project  = var.project_id
  role     = "roles/cloudtrace.agent"
  member   = "serviceAccount:${google_service_account.mcp[each.key].email}"
}

# ── MCP Invoker SA — agent impersonates this to call Cloud Run ─────────────────
# Agent Identity (principalSet://...) cannot invoke Cloud Run directly.
# The agent holds Token Creator on this SA, mints an OIDC token, and Cloud Run
# sees this SA as the caller.

resource "google_service_account" "mcp_invoker" {
  account_id   = "fsi-rm-mcp-invoker"
  display_name = "FSI-RM Agent MCP Invoker"
}

# Invoker SA can call each MCP Cloud Run service
resource "google_project_iam_member" "invoker_run" {
  project = var.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.mcp_invoker.email}"
}

# ── Agent Identity IAM (set after deploy_agent.py runs) ───────────────────────

locals {
  # principalSet URI for the deployed Agent Engine
  agent_identity_principal = var.agent_engine_id != "" ? (
    "principalSet://agents.global.org-${var.organization_id}.system.id.goog/attribute.platformContainer/aiplatform/projects/${var.project_number}"
  ) : null
}

resource "google_project_iam_member" "agent_aiplatform" {
  count   = var.agent_engine_id != "" ? 1 : 0
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "principalSet://${local.agent_identity_principal}"
}

resource "google_project_iam_member" "agent_registry_viewer" {
  count   = var.agent_engine_id != "" ? 1 : 0
  project = var.project_id
  role    = "roles/agentregistry.viewer"    # allows list_mcp_servers()
  member  = "principalSet://${local.agent_identity_principal}"
}

resource "google_project_iam_member" "agent_token_creator" {
  count   = var.agent_engine_id != "" ? 1 : 0
  project = var.project_id
  role    = "roles/iam.serviceAccountTokenCreator"   # allows impersonation of mcp_invoker
  member  = "principalSet://${local.agent_identity_principal}"
}

# ── Agent Gateway ─────────────────────────────────────────────────────────────
# Google-managed gateway — sits between Agent Engine and MCP Cloud Run services.
# Enforces IAP (REQUEST_AUTHZ) and Model Armor (CONTENT_AUTHZ) on every MCP call.

resource "google_network_services_agent_gateway" "fsi_rm" {
  provider  = google-beta
  count     = var.enable_agent_gateway ? 1 : 0
  name      = "fsi-rm-gateway"
  protocols = ["MCP"]

  google_managed {
    governed_access_path = "AGENT_TO_ANYWHERE"
  }

  depends_on = [google_project_service.apis]
}

# IAP authorization extension — per-tool IAM enforcement via Agent Identity
resource "google_network_services_authz_extension" "iap" {
  provider  = google-beta
  count     = var.enable_agent_gateway ? 1 : 0
  name      = "fsi-rm-iap-authz"
  location  = var.region
  service   = "iap.googleapis.com"
  metadata  = { iamEnforcementMode = "ENFORCED" }
}

resource "google_network_services_authz_policy" "iap" {
  provider       = google-beta
  count          = var.enable_agent_gateway ? 1 : 0
  name           = "fsi-rm-iap-policy"
  location       = var.region
  target         = google_network_services_agent_gateway.fsi_rm[0].id
  action         = "CUSTOM"
  policy_profile = "REQUEST_AUTHZ"

  custom_provider {
    authz_extension {
      resources = [google_network_services_authz_extension.iap[0].id]
    }
  }
}

# Model Armor extension — prompt/response DLP screening
resource "google_network_services_authz_extension" "model_armor" {
  provider  = google-beta
  count     = (var.enable_agent_gateway && var.enable_model_armor) ? 1 : 0
  name      = "fsi-rm-model-armor-authz"
  location  = var.region
  service   = "modelarmor.${var.region}.rep.googleapis.com"
  metadata  = {
    model_armor_settings = jsonencode([{
      request_template_id  = "fsi-rm-request-template"
      response_template_id = "fsi-rm-response-template"
    }])
  }
}

resource "google_network_services_authz_policy" "model_armor" {
  provider       = google-beta
  count          = (var.enable_agent_gateway && var.enable_model_armor) ? 1 : 0
  name           = "fsi-rm-model-armor-policy"
  location       = var.region
  target         = google_network_services_agent_gateway.fsi_rm[0].id
  action         = "CUSTOM"
  policy_profile = "CONTENT_AUTHZ"

  custom_provider {
    authz_extension {
      resources = [google_network_services_authz_extension.model_armor[0].id]
    }
  }
}

# ── Cloud Run MCP Services ─────────────────────────────────────────────────────

locals {
  mcp_server_modules = {
    "fsi-rm-core-banking-mcp" = "core_banking_mcp"
    "fsi-rm-portfolio-mcp"    = "portfolio_mcp"
    "fsi-rm-comms-mcp"        = "comms_mcp"
    "fsi-rm-compliance-mcp"   = "compliance_mcp"
    "fsi-rm-voice-mcp"        = "voice_mcp"
  }
}

resource "google_cloud_run_v2_service" "mcp" {
  for_each = local.mcp_server_modules
  name     = each.key
  location = var.region

  ingress = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.mcp[each.key].email

    containers {
      image = "gcr.io/${var.project_id}/fsi-rm-mcp:latest"
      env {
        name  = "MCP_SERVER"
        value = each.value
      }
      env {
        name  = "GCP_PROJECT"
        value = var.project_id
      }
      env {
        name  = "BQ_DATASET"
        value = "fsi_rm_poc"
      }
      env {
        name  = "OTEL_SERVICE_NAME"
        value = each.key
      }
      resources {
        limits = { cpu = "1", memory = "512Mi" }
      }
    }

    scaling {
      min_instance_count = 1
      max_instance_count = 5
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
      client,
      client_version,
    ]
  }

  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_service_iam_member" "invoker" {
  for_each = local.mcp_server_modules
  project  = var.project_id
  location = var.region
  service  = google_cloud_run_v2_service.mcp[each.key].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.mcp_invoker.email}"
}

# ── Agent Registry — register MCP servers ─────────────────────────────────────
# The agent discovers these at runtime via AgentRegistry.list_mcp_servers().
# Re-registers whenever MCP service URLs change.

resource "null_resource" "agent_registry" {
  triggers = {
    mcp_services = jsonencode(local.mcp_images)
    project_id   = var.project_id
    location     = var.region
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -euo pipefail
      for svc in fsi-rm-core-banking-mcp fsi-rm-portfolio-mcp fsi-rm-comms-mcp fsi-rm-compliance-mcp fsi-rm-voice-mcp; do
        url=$(gcloud run services describe "$svc" \
          --project=${var.project_id} --region=${var.region} \
          --format="value(status.url)" 2>/dev/null || echo "")
        if [ -z "$url" ]; then
          echo "WARNING: Cloud Run service $svc not yet deployed — skipping registry"
          continue
        fi
        echo "Registering $svc → $url/mcp"
        gcloud agent-registry mcp-servers create "$svc" \
          --project=${var.project_id} \
          --location=${var.region} \
          --display-name="$svc" \
          --endpoint-url="$url/mcp" \
          --quiet 2>/dev/null || \
        gcloud agent-registry mcp-servers update "$svc" \
          --project=${var.project_id} \
          --location=${var.region} \
          --endpoint-url="$url/mcp" \
          --quiet
      done
    EOT
  }

  depends_on = [google_cloud_run_v2_service.mcp]
}

# ── Outputs ───────────────────────────────────────────────────────────────────

output "agent_gateway_id" {
  value = var.enable_agent_gateway ? google_network_services_agent_gateway.fsi_rm[0].id : "disabled"
}

output "mcp_invoker_sa" {
  value = google_service_account.mcp_invoker.email
}

output "mcp_service_urls" {
  value = { for k, v in google_cloud_run_v2_service.mcp : k => v.uri }
}

output "null_resource_trigger" {
  value = null_resource.agent_registry.triggers
}

output "deploy_command" {
  value = <<-EOT
    python agents/orchestrator/deploy_agent.py \
      --project=${var.project_id} \
      --location=${var.region} \
      --agent-gateway=${var.enable_agent_gateway ? google_network_services_agent_gateway.fsi_rm[0].id : ""} \
      --invoker-sa=${google_service_account.mcp_invoker.email} \
      --enable-agent-identity
  EOT
}
