#!/bin/bash
# Grant per-MCP IAP egress IAM bindings for the deployed Agent Identity.
#
# Must be run AFTER deploy_agent.py succeeds and AGENT_ENGINE_ID is known.
# Adapted from GoogleCloudPlatform/cloud-networking-solutions/demos/agent-gateway
#
# Usage:
#   AGENT_ENGINE_ID=<id> bash scripts/grant_agent_mcp_egress.sh
#   # or filter to specific services:
#   AGENT_ENGINE_ID=<id> MCP_FILTER="core-banking-mcp portfolio-mcp" bash scripts/grant_agent_mcp_egress.sh

set -euo pipefail
source "$(dirname "$0")/../.env" 2>/dev/null || true

PROJECT_ID="${GCP_PROJECT:-<YOUR_GCP_PROJECT>}"
LOCATION="${GCP_LOCATION:-asia-south1}"
ORG_ID="${GCP_ORG_ID:-}"
PROJECT_NUMBER="${GCP_PROJECT_NUMBER:-}"
AGENT_ENGINE_ID="${AGENT_ENGINE_ID:-}"
MCP_FILTER="${MCP_FILTER:-core-banking-mcp portfolio-mcp comms-mcp compliance-mcp voice-mcp}"

if [[ -z "$AGENT_ENGINE_ID" ]]; then
  echo "ERROR: AGENT_ENGINE_ID is required (output from deploy_agent.py)" >&2
  exit 1
fi
if [[ -z "$ORG_ID" || -z "$PROJECT_NUMBER" ]]; then
  echo "ERROR: GCP_ORG_ID and GCP_PROJECT_NUMBER must be set" >&2
  exit 1
fi

AGENT_PRINCIPAL="principalSet://agents.global.org-${ORG_ID}.system.id.goog/attribute.platformContainer/aiplatform/projects/${PROJECT_NUMBER}"

echo "Agent Identity principal: $AGENT_PRINCIPAL"
echo "Granting roles/iap.httpsResourceAccessor per MCP service..."

for svc in $MCP_FILTER; do
  echo "  → $svc"
  gcloud run services add-iam-policy-binding "$svc" \
    --project="$PROJECT_ID" \
    --region="$LOCATION" \
    --member="$AGENT_PRINCIPAL" \
    --role="roles/iap.httpsResourceAccessor" \
    --quiet
done

echo "Done. IAP egress bindings applied for: $MCP_FILTER"
