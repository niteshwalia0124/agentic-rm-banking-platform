#!/bin/bash
# FSI-RM PoC — One-shot setup script
# Usage: ./scripts/setup.sh <GCP_PROJECT_ID>
set -euo pipefail

PROJECT=${1:-$(gcloud config get-value project)}
REGION="asia-south1"
DATASET="fsi_rm_poc"

echo "=== FSI-RM PoC Setup ==="
echo "Project: $PROJECT | Region: $REGION"

# 1. Enable APIs
echo ">> Enabling GCP APIs..."
gcloud services enable \
  aiplatform.googleapis.com \
  bigquery.googleapis.com \
  run.googleapis.com \
  cloudtrace.googleapis.com \
  monitoring.googleapis.com \
  --project="$PROJECT"

# 2. BigQuery dataset
echo ">> Creating BigQuery dataset..."
bq mk --dataset \
  --location="$REGION" \
  --description="FSI-RM PoC mock banking data" \
  "${PROJECT}:${DATASET}" 2>/dev/null || echo "Dataset already exists"

# 3. Create tables from schema
echo ">> Creating BigQuery tables..."
bq query \
  --project_id="$PROJECT" \
  --use_legacy_sql=false \
  "$(sed "s/fsi_rm_poc/${DATASET}/g" data/bigquery_schema.sql)"

# 4. Seed mock data
echo ">> Seeding mock data (50 clients)..."
GCP_PROJECT="$PROJECT" BQ_DATASET="$DATASET" python scripts/seed_bigquery.py

# 5. Python env
echo ">> Installing dependencies..."
pip install -r requirements.txt -q

# 6. Export env vars
cat > .env << EOF
GCP_PROJECT=${PROJECT}
GCP_REGION=${REGION}
BQ_DATASET=${DATASET}
GEMINI_MODEL=gemini-2.0-flash-001
CORE_BANKING_MCP_URL=http://localhost:8001
PORTFOLIO_MCP_URL=http://localhost:8002
COMMS_MCP_URL=http://localhost:8003
COMPLIANCE_MCP_URL=http://localhost:8004
EOF

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Start MCP servers:    ./scripts/start_local.sh"
echo "  2. Run agent locally:    adk run agents/orchestrator/"
echo "  3. Deploy to cloud:      ./scripts/deploy.sh $PROJECT"
