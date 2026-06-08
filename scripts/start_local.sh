#!/bin/bash
# Start all 4 MCP servers locally for development
set -euo pipefail

source .env

echo "Starting MCP servers..."

GCP_PROJECT=$GCP_PROJECT BQ_DATASET=$BQ_DATASET PORT=8001 \
  python mcp_servers/core_banking_mcp.py &
PID1=$!
echo "  core-banking-mcp  → http://localhost:8001  (PID $PID1)"

GCP_PROJECT=$GCP_PROJECT BQ_DATASET=$BQ_DATASET PORT=8002 \
  python mcp_servers/portfolio_mcp.py &
PID2=$!
echo "  portfolio-mcp     → http://localhost:8002  (PID $PID2)"

GCP_PROJECT=$GCP_PROJECT BQ_DATASET=$BQ_DATASET PORT=8003 \
  python mcp_servers/comms_mcp.py &
PID3=$!
echo "  comms-mcp         → http://localhost:8003  (PID $PID3)"

GCP_PROJECT=$GCP_PROJECT BQ_DATASET=$BQ_DATASET PORT=8004 \
  python mcp_servers/compliance_mcp.py &
PID4=$!
echo "  compliance-mcp    → http://localhost:8004  (PID $PID4)"

GCP_PROJECT=$GCP_PROJECT BQ_DATASET=$BQ_DATASET PORT=8005 \
  COACH_SERVER_URL=${COACH_SERVER_URL:-http://localhost:8006} \
  PIPECAT_BRIDGE_URL=${PIPECAT_BRIDGE_URL:-http://localhost:8010} \
  python mcp_servers/voice_mcp.py &
PID5=$!
echo "  voice-mcp         → http://localhost:8005  (PID $PID5)"

PORT=8006 \
  uvicorn coach.server:app --host 0.0.0.0 --port 8006 &
PID6=$!
echo "  coach-server      → http://localhost:8006  (PID $PID6)  ← RM dashboard"

GEMINI_LIVE_MODEL=${GEMINI_LIVE_MODEL:-gemini-3.1-flash-live-preview-04-2026} \
  COACH_SERVER_URL=${COACH_SERVER_URL:-http://localhost:8006} \
  uvicorn bridge.pipecat_bridge:app --host 0.0.0.0 --port 8010 &
PID7=$!
echo "  pipecat-bridge    → http://localhost:8010  (PID $PID7)"

echo ""
echo "All servers running. Press Ctrl+C to stop."
echo "Now run in another terminal:  adk run agents/orchestrator/"
echo "Open RM voice-coach dashboard: http://localhost:8006/?call_id=<CALL-ID>"
echo ""

# Cleanup on exit
trap "kill $PID1 $PID2 $PID3 $PID4 $PID5 $PID6 $PID7 2>/dev/null; echo 'All servers stopped.'" EXIT
wait
