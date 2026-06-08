#!/bin/bash
# Deploy all 4 external A2A agents to AWS Bedrock AgentCore
#
# What this replaces: Lambda + FastAPI + Mangum + manual A2A JSON-RPC
# What you get:       AgentCore Runtime (managed containers) +
#                     AgentCore Gateway (native A2A — auto Agent Card, JSON-RPC routing)
#
# Prerequisites:
#   - AWS CLI v2 configured (aws configure)
#   - Docker running
#   - Bedrock AgentCore enabled in your AWS account (us-east-1)
#
# Run: bash external_agents/aws_deploy.sh
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"   # N. Virginia — AgentCore GA region
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
REPO_PREFIX="fsi-rm"

echo "=== Deploying FSI-RM External A2A Agents via AWS Bedrock AgentCore ==="
echo "    Region:  $AWS_REGION"
echo "    Account: $AWS_ACCOUNT_ID"
echo "    ECR:     $ECR_REGISTRY"
echo ""

# ── Authenticate Docker to ECR ────────────────────────────────────────────────
echo "Authenticating Docker to ECR..."
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$ECR_REGISTRY"

# ── Helper: ensure ECR repo exists ───────────────────────────────────────────
ensure_ecr_repo() {
    local repo_name=$1
    aws ecr describe-repositories --repository-names "$repo_name" \
        --region "$AWS_REGION" &>/dev/null \
    || aws ecr create-repository \
        --repository-name "$repo_name" \
        --region "$AWS_REGION" \
        --image-scanning-configuration scanOnPush=true \
        --output text --query 'repository.repositoryUri' | tail -1
}

# ── Helper: build → push Docker image ────────────────────────────────────────
build_and_push() {
    local agent_name=$1   # e.g. "amfi-agent"
    local agent_dir=$2    # absolute path to agent directory

    local repo_name="${REPO_PREFIX}-${agent_name}"
    local image_uri="${ECR_REGISTRY}/${repo_name}:latest"

    echo "▶ Building $agent_name..."
    ensure_ecr_repo "$repo_name"
    docker build -t "$image_uri" "$agent_dir" --quiet
    docker push "$image_uri" --quiet
    echo "  ✓ Pushed: $image_uri"
    echo "$image_uri"
}

# ── Helper: create or update AgentCore Runtime ───────────────────────────────
deploy_runtime() {
    local agent_name=$1
    local image_uri=$2
    local description=$3

    local runtime_name="${REPO_PREFIX}-${agent_name}-runtime"

    echo "  Checking AgentCore Runtime: $runtime_name..."

    # Check if runtime already exists
    if aws bedrock-agentcore-control get-agent-runtime \
          --agent-runtime-identifier "$runtime_name" \
          --region "$AWS_REGION" &>/dev/null; then
        # Update container image
        aws bedrock-agentcore-control update-agent-runtime \
            --agent-runtime-identifier "$runtime_name" \
            --container-configuration "containerUri=${image_uri}" \
            --region "$AWS_REGION" \
            --output text --query 'agentRuntimeArn' | tail -1
        echo "  ✓ Runtime updated"
    else
        # Create new runtime
        RUNTIME_ARN=$(aws bedrock-agentcore-control create-agent-runtime \
            --agent-runtime-name "$runtime_name" \
            --description "$description" \
            --container-configuration "containerUri=${image_uri},port=8080" \
            --network-configuration "networkMode=PUBLIC" \
            --region "$AWS_REGION" \
            --output text --query 'agentRuntimeArn' | tail -1)
        echo "  ✓ Runtime created: $RUNTIME_ARN"
        echo "$RUNTIME_ARN"
    fi

    # Return ARN for gateway creation
    aws bedrock-agentcore-control get-agent-runtime \
        --agent-runtime-identifier "$runtime_name" \
        --region "$AWS_REGION" \
        --output text --query 'agentRuntimeArn' | tail -1
}

# ── Helper: create or update AgentCore Gateway (A2A surface) ─────────────────
# AgentCore Gateway auto-generates Agent Card + A2A JSON-RPC routing.
# No manual FastAPI A2A boilerplate needed.
deploy_gateway() {
    local agent_name=$1
    local runtime_arn=$2
    local display_name=$3
    local description=$4

    local gateway_name="${REPO_PREFIX}-${agent_name}-gateway"

    echo "  Configuring AgentCore Gateway: $gateway_name..."

    if aws bedrock-agentcore-control get-gateway \
          --gateway-identifier "$gateway_name" \
          --region "$AWS_REGION" &>/dev/null; then
        aws bedrock-agentcore-control update-gateway \
            --gateway-identifier "$gateway_name" \
            --agent-runtime-arn "$runtime_arn" \
            --region "$AWS_REGION" \
            --output text --query 'gatewayUrl' | tail -1
    else
        GATEWAY_URL=$(aws bedrock-agentcore-control create-gateway \
            --gateway-name "$gateway_name" \
            --display-name "$display_name" \
            --description "$description" \
            --agent-runtime-arn "$runtime_arn" \
            --protocol-type A2A \
            --auth-type NONE \
            --region "$AWS_REGION" \
            --output text --query 'gatewayUrl' | tail -1)
        echo "  ✓ Gateway created (A2A): $GATEWAY_URL"
        echo "$GATEWAY_URL"
    fi

    aws bedrock-agentcore-control get-gateway \
        --gateway-identifier "$gateway_name" \
        --region "$AWS_REGION" \
        --output text --query 'gatewayUrl' | tail -1
}

# ── Deploy each agent ─────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

deploy_agent() {
    local name=$1
    local dir=$2
    local display=$3
    local desc=$4

    echo ""
    echo "━━━ $display ━━━"

    IMAGE_URI=$(build_and_push "$name" "$dir")
    RUNTIME_ARN=$(deploy_runtime "$name" "$IMAGE_URI" "$desc")
    GATEWAY_URL=$(deploy_gateway "$name" "$RUNTIME_ARN" "$display" "$desc")

    echo "  → A2A endpoint: $GATEWAY_URL"
    echo "  → Agent Card:   ${GATEWAY_URL}/.well-known/agent.json"
    echo ""

    # Emit env var for .env file
    local env_var_name
    env_var_name=$(echo "$name" | tr '[:lower:]-' '[:upper:]_')
    echo "${env_var_name}_URL=${GATEWAY_URL}"
}

# Capture env var lines
AMFI_LINE=$(deploy_agent \
    "amfi-agent" \
    "$SCRIPT_DIR/amfi_agent" \
    "AMFI NAV Agent" \
    "Real mutual fund NAV data from AMFI public API. Serves 1Y/3Y/5Y CAGR returns." \
    | grep "AMFI_AGENT_URL=")

MARKET_LINE=$(deploy_agent \
    "market-data-agent" \
    "$SCRIPT_DIR/market_data_agent" \
    "Market Data Agent" \
    "Real-time NSE/BSE stock prices and indices via Yahoo Finance." \
    | grep "MARKET_DATA_AGENT_URL=")

BUREAU_LINE=$(deploy_agent \
    "credit-bureau-agent" \
    "$SCRIPT_DIR/credit_bureau_agent" \
    "Credit Bureau Agent" \
    "Mock CIBIL/Experian-format credit reports. Swap for real CIBIL API in production." \
    | grep "CREDIT_BUREAU_AGENT_URL=")

AA_LINE=$(deploy_agent \
    "account-aggregator-agent" \
    "$SCRIPT_DIR/account_aggregator_agent" \
    "Account Aggregator Agent" \
    "Mock RBI AA framework data showing cross-bank client financial profile." \
    | grep "ACCOUNT_AGGREGATOR_AGENT_URL=")

# ── Print .env snippet ────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "All 4 agents deployed via AgentCore Runtime + Gateway!"
echo ""
echo "Add these to your .env file:"
echo ""
echo "$AMFI_LINE"
echo "$MARKET_LINE"
echo "$BUREAU_LINE"
echo "$AA_LINE"
echo ""
echo "Then run: python external_agents/test_a2a_agents.py"
echo ""
echo "AgentCore vs Lambda (what changed):"
echo "  - No more Mangum Lambda adapter"
echo "  - No more manual FastAPI A2A JSON-RPC boilerplate"
echo "  - Agent Card auto-generated by AgentCore Gateway"
echo "  - Both GCP (ADK on Agent Engine) and AWS (ADK on AgentCore) use same A2A protocol"
