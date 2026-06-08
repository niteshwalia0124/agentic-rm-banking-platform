# Agent Teams for Relationship Managers

Indian bank RM automation using Gemini Enterprise Agent Platform, ADK v1.0, MCP, A2A, and OpenTelemetry observability.

## Architecture
```
RM (Google Chat) → Agent Gateway → Orchestrator Agent (Agent Engine)
                                        ↓ A2A
                              [Client | Portfolio | Comms | Compliance] Agents
                                        ↓ MCP (OAuth 2.1)
                              [core-banking | portfolio | comms | compliance] MCP servers
                                        ↓
                              BigQuery (mock data) / Real CBS (production)
```

## Stack
- **LLM**: Gemini 2.0 Flash (PoC) → Gemini 3.1 Pro (production)
- **Agents**: Google ADK v1.0 on Vertex AI Agent Engine
- **Tools**: MCP servers on Cloud Run
- **Data**: BigQuery (mock) → CBS/CRM via Private Service Connect (production)
- **Interface**: Google Chat App
- **Observability**: OTel gen_ai.* → Google Cloud Monitoring + Cloud Trace

## Quick Start
```bash
# 1. Setup
pip install -r requirements.txt
cp .env.example .env  # fill in your GCP project ID

# 2. Load mock data
python scripts/seed_bigquery.py

# 3. Start MCP servers locally
python mcp_servers/core_banking_mcp.py &
python mcp_servers/portfolio_mcp.py &

# 4. Run agent locally
adk run agents/orchestrator/

# 5. Deploy to Agent Engine
adk deploy agents/orchestrator/ --project=$GCP_PROJECT --region=asia-south1
```

## Project Structure
```
fsi-rm/
├── agents/              # ADK agent definitions
│   ├── orchestrator/    # Root orchestrator (routes to sub-agents)
│   ├── client_intel/    # Client 360° view
│   ├── portfolio/       # Holdings, SIPs, loans
│   ├── comms/           # Email drafting, scheduling
│   └── compliance/      # KYC, AML, alerts
├── mcp_servers/         # MCP server implementations (Cloud Run)
│   ├── core_banking_mcp.py
│   ├── portfolio_mcp.py
│   ├── comms_mcp.py
│   └── compliance_mcp.py
├── data/                # BigQuery schemas + seed data
├── scripts/             # Setup and deployment scripts
├── infra/               # Terraform for GCP resources
├── tests/               # Agent evaluation tests
└── observability/       # OTel collector config + dashboards
```
