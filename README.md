# 🏦 Financial Services Relationship Manager (FSI-RM) Agentic AI Platform

[![Google Cloud](https://img.shields.io/badge/Google%20Cloud-Vertex%20AI-4285F4?logo=googlecloud&logoColor=white)](https://cloud.google.com/vertex-ai)
[![ADK](https://img.shields.io/badge/Google%20ADK-Agent%20Development%20Kit-34A853?logo=google&logoColor=white)](https://cloud.google.com/vertex-ai/generative-ai/docs/agent-development-kit)
[![Model Context Protocol](https://img.shields.io/badge/Tools-Model%20Context%20Protocol%20(MCP)-009688)](https://modelcontextprotocol.io)
[![Gemini Live API](https://img.shields.io/badge/Voice%20AI-Gemini%20Live%20Multimodal-8E75B2)](https://cloud.google.com/vertex-ai/generative-ai/docs/multimodal-live-api)
[![OpenTelemetry](https://img.shields.io/badge/Observability-OpenTelemetry-F54A00?logo=opentelemetry&logoColor=white)](https://opentelemetry.io)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

An enterprise-grade, multi-agent AI platform designed to empower bank Relationship Managers (RMs) using **Google Agent Development Kit (ADK)**, **Model Context Protocol (MCP)**, and the **Gemini Live API** for real-time multilingual telephony. 

This repository showcases a complete reference architecture for modern corporate and wealth banking automation—designed for low-latency performance, strict compliance verification, seamless cross-agent delegation (A2A), and comprehensive OpenTelemetry-based observability.

---

## 🏛️ High-Level System Architecture

![FSI-RM Agentic Banking Platform Architecture](docs/architecture.jpg)

### Architectural Overview

The platform is structured into four core architectural layers:

1. **Client & Channel Tier**:
   - **RM Command Dashboard & Coach Portal**: Web dashboard presenting real-time client portfolios, call sentiment metrics, and live in-call coaching suggestions.
   - **Google Chat Interface**: Conversational entry point for RMs to trigger proactive audits and outreach.
   - **Outbound Telephony Streams**: Real-time dual-sided PSTN audio streams via Twilio Media Streams.

2. **Gateway & Telephony Broker Tier**:
   - **A2A Gateway Server on Cloud Run**: Ingress router managing Agent-to-Agent communication protocols across the agent swarm.
   - **LiveAPI Broker Bridge**: FastAPI + Pipecat-powered WebSocket bridge connecting Twilio PSTN audio to the Vertex AI Gemini Live API.

3. **ADK Multi-Agent Orchestration Tier (Google Cloud Agent Runtime)**:
   - **Root Orchestrator**: Evaluates incoming RM prompts and delegates tasks to domain specialists via declarative A2A routing.
   - **Client Intel Agent**: Builds 360° client profiles and cross-examines historical transaction ledgers.
   - **Portfolio & Wealth Agent**: Tracks mutual fund holdings, demat accounts, and impending SIP renewal lapses.
   - **Comms Composition Agent**: Automatically drafts personalized customer emails, WhatsApp updates, and Google Calendar meeting invites.
   - **Compliance & KYC Agent**: Performs automated KYC/AML audits, Days Past Due (DPD) risk assessments, and compliance letter drafting.
   - **Voice Telephony & Voice Coach Agents**: Executes automated telephony conversations and provides live guidance hints on the RM screen.

4. **Model Context Protocol (MCP) Tools & Multicloud Data Tier**:
   - **5 Dedicated MCP Servers**: `core_banking_mcp`, `portfolio_mcp`, `comms_mcp`, `compliance_mcp`, and `voice_mcp`.
   - **Systems of Record**: Google BigQuery data warehouse, Cloud SQL (PostgreSQL), Cloud Firestore, and GCS transcript storage.
   - **Enterprise Observability**: OpenTelemetry (`OTel`) collector exporting `gen_ai.*` execution metrics and tool traces to Cloud Monitoring.

---

## 📖 Business Use Cases & Operational Flows

### 1. Proactive Portfolio & Wealth Management (SIP Renewals)
- **Challenge**: RMs spend hours identifying upcoming Mutual Fund Systematic Investment Plan (SIP) lapses, checking if the client has enough ledger balance, and manually drafting portfolio rebalancing suggestions.
- **Agentic Flow**:
  1. The **Orchestrator** triggers a portfolio review request.
  2. The **Portfolio Agent** queries the Portfolio MCP to fetch active holdings, upcoming SIP schedules, and historical performance.
  3. If a ledger deficit threatens an upcoming SIP, the **Client Intel Agent** builds a 360° profile of the client.
  4. The **Comms Agent** automatically drafts a highly personalized email suggesting portfolio rebalancing options to fund the SIP, complete with a scheduled Google Calendar reminder for the RM.

### 2. Proactive Risk, KYC & Compliance Remediation
- **Challenge**: Banking compliance requires immediate response to KYC/AML issues, overdue credit payments (Days Past Due - DPD alerts), or anomalous transactions.
- **Agentic Flow**:
  1. The **Compliance Agent** detects a risk event (e.g., an expired KYC document or a pending DPD alert).
  2. It delegates to the **Client Intel Agent** to cross-examine core transaction history and customer tiers.
  3. It drafts a formal compliance rectification letter, pre-validating it against the bank's internal policy rules via the **Compliance MCP**, and flags high-risk transactions directly inside the RM’s dashboard.

### 3. Outbound Multilingual Telephony Outreach (Twilio + Gemini Live)
- **Challenge**: RMs cannot manually scale live phone outreach to hundreds of clients for urgent updates.
- **Agentic Flow**:
  1. The RM triggers an automated outbound voice alert via Google Chat or Dashboard.
  2. The **Voice Agent** validates the script and passes context to the **Voice MCP**.
  3. The **Voice MCP** initiates an outbound PSTN call via **Twilio**.
  4. When the client answers, Twilio streams real-time audio via WebSockets Media Streams to the **LiveAPI Broker**, which connects to the **Gemini Live API** for a natural, ultra-low-latency conversation in multiple languages (Hindi, Indian English, etc.).
  5. The RM receives live transcript updates and recommended coaching tips on their dashboard in real time.

---

## 📂 Project Structure

```text
agentic-rm-banking-platform/
├── docs/
│   └── architecture.jpg        # High-level architecture block diagram
├── images/
│   └── HLD.png                 # Architecture visual asset
├── agents/                     # Core ADK Agent Configurations & Logic
│   ├── orchestrator/           # Root Routing Agent (routes queries using A2A)
│   ├── client_intel/           # Client 360° Insight Agent
│   ├── portfolio/              # Wealth, SIP, and demat holdings manager
│   ├── comms/                  # Communication composition & delivery (Email/Chat)
│   ├── compliance/             # KYC, transaction auditing & risk controller
│   ├── voice/                  # Telephony assistant agent
│   └── voice_coach/            # Human-in-the-loop dashboard agent
├── mcp_servers/                # Model Context Protocol (MCP) tool servers
│   ├── core_banking_mcp.py     # Connects agents to Customer Core Banking data
│   ├── portfolio_mcp.py        # Connects agents to Mutual Fund & SIP ledger
│   ├── comms_mcp.py            # Connects agents to Gmail and WhatsApp tools
│   ├── compliance_mcp.py       # Connects agents to KYC registers and rule-checks
│   └── voice_mcp.py            # Triggers outbound Twilio PSTN voice dials
├── gateway/                    # Main API Ingress and Agent-to-Agent entry
│   └── a2a_server.py           # Gateway server exposing A2A routes for Vertex AI
├── coach/                      # Live Human-in-the-loop assistant
│   ├── server.py               # Event subscriber feeding live advice hints
│   └── dashboard.html          # Visual dashboard presenting real-time transcripts
├── data/                       # Schema and Mock Data seeding configurations
│   └── bigquery_schema.sql     # SQL DDL schemas for core mock banking systems
├── infra/                      # Infrastructure as Code (Terraform)
│   └── main.tf                 # Provisions Cloud Run, BigQuery, and PSC
├── requirements.txt            # Package dependencies
└── .env.example                # Configuration placeholders (GCP, Twilio)
```

---

## 🚀 Getting Started (Local Development)

### 1. Set Up Environment
Ensure you have Python 3.10+ installed:

```bash
git clone https://github.com/niteshwalia0124/agentic-rm-banking-platform.git
cd agentic-rm-banking-platform

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Local Settings
```bash
cp .env.example .env
# Fill in your GCP_PROJECT, and optional TWILIO credentials
```

### 3. Initialize BigQuery Database
```bash
python scripts/seed_bigquery.py
```

### 4. Run MCP Tool Servers Locally
```bash
python mcp_servers/core_banking_mcp.py &
python mcp_servers/portfolio_mcp.py &
python mcp_servers/compliance_mcp.py &
```

### 5. Launch Interactive Playground
```bash
adk playground agents/orchestrator/
```

---

## ☁️ Deployment (Google Cloud)

Deploy the entire platform using Terraform and Cloud Build:

```bash
# 1. Provision GCP infrastructure
cd infra
terraform init
terraform apply -var project_id="<YOUR_GCP_PROJECT_ID>"
cd ..

# 2. Build and deploy container images
gcloud builds submit --config cloudbuild-mcp.yaml
gcloud builds submit --config cloudbuild-gateway.yaml

# 3. Deploy the Orchestrator to Vertex AI Agent Engine
python agents/orchestrator/deploy_agent.py --project="<YOUR_GCP_PROJECT_ID>" --location="us-central1"
```

---

## 📈 Observability & Evaluation

The platform exports OpenTelemetry traces with `gen_ai.*` semantic conventions directly to Google Cloud Monitoring:

```bash
# Run unit and mock integration tests
pytest tests/

# Execute agent trajectory evaluations against compliance benchmarks
adk eval agents/orchestrator/ tests/evalsets/compliance_digest.evalset.json
```

---

## 📄 License

This project is licensed under the Apache 2.0 License - see the [LICENSE](LICENSE) file for details.
