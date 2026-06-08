#!/bin/bash
# Deploy MCP servers to Cloud Run and agent to Agent Engine
set -euo pipefail

PROJECT=${1:-$(gcloud config get-value project)}
REGION="asia-south1"

echo "=== Deploying FSI-RM to GCP ==="
echo "Project: $PROJECT | Region: $REGION"

# Build & push MCP server images
for SERVER in core_banking portfolio comms compliance; do
  NAME="${SERVER/_/-}-mcp"
  IMAGE="gcr.io/$PROJECT/$NAME:latest"
  echo ">> Building $NAME..."
  gcloud builds submit \
    --tag "$IMAGE" \
    --gcs-log-dir="gs://${PROJECT}-build-logs" \
    --project="$PROJECT" \
    --dockerfile="mcp_servers/Dockerfile" \
    --substitutions="_MCP_SERVER=${SERVER}_mcp" \
    . --quiet

  echo ">> Deploying $NAME to Cloud Run ($REGION)..."
  gcloud run deploy "$NAME" \
    --image="$IMAGE" \
    --region="$REGION" \
    --platform=managed \
    --no-allow-unauthenticated \
    --service-account="fsi-rm-mcp-servers@${PROJECT}.iam.gserviceaccount.com" \
    --set-env-vars="GCP_PROJECT=${PROJECT},BQ_DATASET=fsi_rm_poc" \
    --memory=512Mi \
    --cpu=1 \
    --project="$PROJECT" \
    --quiet

  URL=$(gcloud run services describe "$NAME" \
    --region="$REGION" --project="$PROJECT" \
    --format="value(status.url)")
  echo "  Deployed: $URL"
done

# Deploy orchestrator agent to Agent Engine
echo ">> Deploying orchestrator to Vertex AI Agent Engine..."
cd agents/orchestrator
adk deploy . \
  --project="$PROJECT" \
  --region="$REGION"
cd ../..

echo ""
echo "=== Deployment complete ==="
echo "Test with: adk run agents/orchestrator/ --remote"
