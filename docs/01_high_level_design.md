# High Level Design: Agent Teams for Relationship Managers

**Document Version:** 1.0
**Date:** 2026-05-29
**Author:** Nitesh Walia
**Status:** Production

---

## Table of Contents

1. Executive Summary
2. System Overview
3. Architecture Pillars
4. Component Architecture
5. Technology Stack
6. Deployment Topology
7. Security Model
8. Data Flows
9. Compliance and Governance
10. Key Design Decisions
11. Known Limitations and Roadmap

---

## 1. Executive Summary

**Agent Teams for Relationship Managers** is a production-deployed, multi-agent AI system built to augment the capability of bank Relationship Managers (RMs). Rather than navigating four or five disconnected banking systems, an RM types a single natural language prompt into Gemini Enterprise (Agentspace), and a coordinated team of AI agents retrieves, synthesizes, and acts on data spanning core banking, portfolio management, compliance, credit bureaus, and market data — across two cloud providers.

The system is live in production as of May 2026. The primary demonstration scenario involves RM Nitesh Walia (RM001) and client Amit Joshi (C0022), where a six-prompt end-to-end workflow exercises every agent, every MCP server, every cross-cloud call, and the real-time voice capability.

The system embodies three core design beliefs:

- **AI should augment, not replace, the RM.** Every action that touches a client — a call, a WhatsApp message, an email — requires explicit RM approval before execution.
- **Multi-agent specialization beats monolithic agents.** Five specialist sub-agents, each with a narrow domain and its own MCP server, outperform a single large agent trying to know everything.
- **Cross-cloud is a feature, not a compromise.** AWS Bedrock AgentCore hosts regulated Indian financial data agents (AMFI, BSE/NSE, CIBIL, RBI AA Framework) where AWS tooling offers the best fit, while GCP hosts the orchestration and real-time voice stack.

---

## 2. System Overview

### 2.1 Purpose

Financial services relationship management requires synthesizing data from regulatory bodies (AMFI, CIBIL, RBI Account Aggregator), market feeds (BSE/NSE), core banking systems, and CRM history — then communicating with clients through multiple channels (WhatsApp, voice calls, email). Today this demands an RM context-switch across many systems. This system collapses that into a single conversational interface.

### 2.2 Scope

The system covers:

- Client portfolio analysis and recommendation workflows
- Compliance checks (KYC status, regulatory flags, risk profiling)
- Market and NAV data retrieval for holdings-level analysis
- Credit bureau lookups (CIBIL/Experian scores and reports)
- Account aggregation across banks via the RBI AA Framework
- Outbound real-time voice calls in Hindi using Gemini Live API
- WhatsApp voice note dispatch with server-side locked scripts
- Conversational memory across sessions via a persistent Memory Bank

### 2.3 Actors

| Actor | Role |
|---|---|
| Relationship Manager (RM) | Primary user; types prompts, approves actions |
| Gemini Enterprise (Agentspace) | RM-facing chat interface, managed by Google |
| Orchestrator Agent | Intent router; delegates to specialist sub-agents |
| Specialist Sub-agents (5) | Domain experts: compliance, client intel, portfolio, comms, voice |
| MCP Servers (5) | Tool execution layer for each sub-agent domain |
| AWS Bedrock Agents (4) | Regulated data providers: AMFI, Market Data, Credit Bureau, AA |
| Client (e.g., Amit Joshi C0022) | End recipient of communications; does not interact with the system directly |

---

## 3. Architecture Pillars

### Pillar 1: Hierarchical Agent Orchestration

The system uses a two-tier agent hierarchy. A single Orchestrator Agent (LlmAgent, Gemini 2.5 Flash) receives all RM intent and decomposes it into sub-tasks, each delegated to a specialist sub-agent. Sub-agents do not communicate with each other directly; all coordination flows through the Orchestrator. This prevents unintended agent interactions and makes the reasoning trace auditable.

The Orchestrator holds a PreloadMemoryTool that injects client-specific and RM-specific memory context at the start of every session, so it never enters a conversation cold.

### Pillar 2: Protocol Separation

Three distinct protocols handle different communication layers, and they are never mixed:

- **A2A (JSON-RPC 2.0):** Agent-to-agent communication. Used between Gemini Enterprise and the Gateway, Gateway and Agent Engine, and GCP agents and AWS Bedrock agents.
- **MCP (StreamableHTTP, OAuth 2.1):** Agent-to-tool communication. Each sub-agent calls its MCP server over StreamableHTTP with impersonated service account tokens.
- **WebSocket:** Real-time bidirectional audio. Used exclusively for the Twilio Media Streams to LiveAPI Broker to Gemini Live API pipeline.

### Pillar 3: Human-in-the-Loop for All Outbound Actions

No client-facing action executes without RM approval. The comms_agent and voice_agent surface proposed actions (message text, call script, note_id) to the Orchestrator, which presents them to the RM for confirmation. The RM's "yes" or "go ahead" triggers the actual send. This applies to:

- WhatsApp text messages
- WhatsApp voice notes
- Outbound phone calls

### Pillar 4: Server-Side Action Locking

Voice note scripts and templated communications are locked server-side. An agent references a `note_id` (e.g., `sip_reminder_hi_001`) rather than generating free-form audio content. The MCP server resolves the note_id to the approved script and generates TTS audio. This prevents the LLM from hallucinating or altering compliance-approved communication scripts.

### Pillar 5: Cross-Cloud Data Sovereignty

Regulated Indian financial data — mutual fund NAV data (AMFI), exchange prices (BSE/NSE), credit scores (CIBIL/Experian), and cross-bank account data (RBI AA Framework) — is served by agents running on AWS Bedrock AgentCore in us-east-1. GCP Orchestrator agents call these via A2A over SigV4-authenticated HTTPS. This separation acknowledges that different data domains may be better served by different cloud providers while maintaining a unified orchestration plane.

### Pillar 6: Zero Cold Starts in Production

All Cloud Run services are configured with `min-instances=1`. The Vertex AI Reasoning Engine session is pre-warmed. This is a deliberate operational choice: an RM in a client meeting cannot wait 8-15 seconds for a container to cold-start before getting a response.

---

## 4. Component Architecture

### 4.1 Component Diagram (Text Description)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  RM INTERFACE                                                               │
│  Gemini Enterprise (Agentspace)  — browser/mobile chat                      │
└───────────────────────────┬─────────────────────────────────────────────────┘
                            │  A2A JSON-RPC 2.0 (HTTPS)
                            ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  GCP — us-central1 (unless noted)                                           │
│                                                                             │
│  ┌──────────────────────┐                                                   │
│  │  fsi-rm-a2a-gateway  │  Cloud Run — A2A session broker                  │
│  │  (Cloud Run)         │  Validates contextId, routes to Agent Engine      │
│  └──────────┬───────────┘                                                   │
│             │  A2A JSON-RPC 2.0                                             │
│             ▼                                                               │
│  ┌──────────────────────────────────────────────────────────┐               │
│  │  Vertex AI Reasoning Engine (ID: 8386758037326528512)    │               │
│  │                                                          │               │
│  │  ┌─────────────────────────────────────────────────┐    │               │
│  │  │  Orchestrator Agent (LlmAgent, Gemini 2.5 Flash) │    │               │
│  │  │  - PreloadMemoryTool (Memory Bank)               │    │               │
│  │  │  - Routes to 5 sub-agents                        │    │               │
│  │  └──────┬────────┬─────────┬──────────┬────────────┘    │               │
│  │         │        │         │          │                  │               │
│  │    ┌────▼──┐ ┌───▼───┐ ┌──▼────┐ ┌───▼───┐ ┌────────┐  │               │
│  │    │compli │ │client │ │portfo │ │ comms │ │ voice  │  │               │
│  │    │ance   │ │_intel │ │lio    │ │_agent │ │_agent  │  │               │
│  │    │_agent │ │_agent │ │_agent │ │       │ │        │  │               │
│  │    └───┬───┘ └───┬───┘ └───┬───┘ └───┬───┘ └────┬───┘  │               │
│  └────────┼─────────┼─────────┼─────────┼──────────┼───────┘               │
│           │ MCP     │ MCP     │ MCP     │ MCP      │ MCP                   │
│           │(SHTTP)  │(SHTTP)  │(SHTTP)  │(SHTTP)   │(SHTTP)               │
│           ▼         ▼         ▼         ▼          ▼                       │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐ ┌────────────────┐  │
│  │compliance│ │core-     │ │portfolio │ │comms-mcp   │ │voice-mcp       │  │
│  │-mcp      │ │banking   │ │-mcp      │ │(us-east1)  │ │(us-east1)      │  │
│  │(us-east1)│ │-mcp      │ │(us-east1)│ │            │ │                │  │
│  └────┬─────┘ │(us-east1)│ └────┬─────┘ └─────┬──────┘ └───────┬────────┘  │
│       │       └────┬─────┘      │              │                │           │
│       │            │            │              │                │           │
│       │       ┌────▼─────────────────┐         │          ┌─────▼────────┐  │
│       │       │  BigQuery            │         │          │  LiveAPI     │  │
│       │       │  fsi_rm_poc dataset  │         │          │  Broker      │  │
│       │       │  clients, accounts,  │         │          │  (us-east1)  │  │
│       │       │  mf_holdings,        │         │          └──────┬───────┘  │
│       │       │  sip_mandates,       │         │                 │          │
│       │       │  loans, kyc_docs,    │         │                 │ WS       │
│       │       │  crm_interactions,   │         │                 ▼          │
│       │       │  transactions        │         │          Gemini Live API   │
│       │       └──────────────────────┘         │          (real-time voice) │
│       │                                        │                            │
│       │                              ┌─────────▼──────────────────────────┐ │
│       │                              │  Twilio                            │ │
│       │                              │  - WhatsApp Business API           │ │
│       │                              │  - Media Streams (WebSocket)       │ │
│       │                              │  - Outbound calls                  │ │
│       │                              └────────────────────────────────────┘ │
│                                                                             │
│  GCS Buckets: fsi-rm-voice-notes  |  fsi-rm-call-transcripts              │
└────────────────────────────────┬────────────────────────────────────────────┘
                                 │  A2A JSON-RPC 2.0 + SigV4 (HTTPS)
                                 ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  AWS — us-east-1 (Bedrock AgentCore)                                        │
│                                                                             │
│  ┌─────────────────┐  ┌──────────────────┐  ┌─────────────────┐            │
│  │  AMFI Agent     │  │  Market Data     │  │  Credit Bureau  │            │
│  │  (NAV data)     │  │  Agent           │  │  Agent          │            │
│  │                 │  │  (BSE/NSE)       │  │  (CIBIL/Experian│            │
│  └─────────────────┘  └──────────────────┘  └─────────────────┘            │
│  ┌─────────────────────────────────────────────────────────────┐            │
│  │  Account Aggregator Agent (RBI AA Framework — cross-bank)   │            │
│  └─────────────────────────────────────────────────────────────┘            │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 4.2 Agent Responsibilities

**Orchestrator Agent**
- Entry point for all RM prompts
- Loads Memory Bank context via PreloadMemoryTool at session start
- Classifies intent and delegates to one or more sub-agents
- Aggregates sub-agent responses before presenting to RM
- Presents proposed actions for RM approval before execution

**compliance_agent**
- KYC status checks (documents, expiry, risk category)
- Regulatory flag lookups
- Queries: fsi-rm-compliance-mcp → BigQuery kyc_documents table
- Calls AWS Credit Bureau Agent for CIBIL/Experian data

**client_intel_agent**
- Client 360 view: personal profile, relationship history, CRM notes
- Queries: fsi-rm-core-banking-mcp → BigQuery clients, accounts, crm_interactions tables
- Calls AWS Account Aggregator Agent for cross-bank data

**portfolio_agent**
- Holdings analysis: MF holdings, SIPs, loans, FD/savings accounts
- NAV and current market value computation
- Queries: fsi-rm-portfolio-mcp → BigQuery mf_holdings, sip_mandates, loans, accounts
- Calls AWS AMFI Agent for current NAV data
- Calls AWS Market Data Agent for BSE/NSE prices

**comms_agent**
- Drafts and dispatches WhatsApp text messages and voice notes
- Queries: fsi-rm-comms-mcp → Twilio WhatsApp Business API, GCS fsi-rm-voice-notes
- Voice notes reference server-locked note_id; TTS generated via Gemini 3.1 Flash
- All sends require RM approval before execution

**voice_agent**
- Initiates outbound phone calls via Twilio
- Hands off to LiveAPI Broker for real-time Hindi conversation
- LiveAPI Broker bridges Twilio Media Streams (WebSocket) to Gemini Live API (WebSocket)
- Call transcripts auto-saved to GCS fsi-rm-call-transcripts on disconnect
- Queries: fsi-rm-voice-mcp

---

## 5. Technology Stack

| Layer | Component | Technology | Deployment |
|---|---|---|---|
| RM Interface | Chat UI | Gemini Enterprise (Agentspace) | Google-managed SaaS |
| Gateway | A2A Gateway | Python, FastAPI, Cloud Run | GCP us-central1 |
| Orchestration | Agent Engine | Vertex AI Reasoning Engine | GCP us-central1 |
| Agent Framework | All agents | Google ADK, LlmAgent | Vertex AI Reasoning Engine |
| LLM (orchestration) | Intent routing, synthesis | Gemini 2.5 Flash | Vertex AI |
| LLM (voice) | Real-time conversation | Gemini 3 Live API | Vertex AI |
| LLM (TTS) | Voice note generation | Gemini 3.1 Flash TTS | Vertex AI |
| MCP Servers (5) | Tool execution | Python, MCP StreamableHTTP, Cloud Run | GCP us-east1 |
| Voice Bridge | LiveAPI Broker | Python, WebSocket, Cloud Run | GCP us-east1, max-instances=1 |
| Database | Structured data | BigQuery (fsi_rm_poc dataset) | GCP |
| Object Storage | Audio, transcripts | Google Cloud Storage | GCP |
| Communication | WhatsApp + Voice calls | Twilio Business API, Media Streams | Twilio-managed |
| Cross-cloud agents | AMFI, Market, Credit, AA | AWS Bedrock AgentCore | AWS us-east-1 |
| Cross-cloud auth | GCP → AWS | AWS SigV4 | IAM |
| Agent-to-agent protocol | All A2A calls | A2A JSON-RPC 2.0 | HTTPS |
| Agent-to-tool protocol | All MCP calls | MCP StreamableHTTP, OAuth 2.1 | HTTPS |
| Real-time audio | Twilio ↔ Gemini | WebSocket (bidirectional) | Persistent connection |
| Audio format (voice notes) | WhatsApp audio | OGG/Opus | GCS |
| Audio format (calls) | Twilio ↔ Gemini | PCM 24kHz (mulaw bridge) | In-memory |
| Memory | Session memory | Persistent Memory Bank | Vertex AI |
| Naming convention | All GCP resources | `fsi-rm-` prefix | GCP project |

---

## 6. Deployment Topology

### 6.1 GCP Region Layout

All production services run in GCP with the following regional split:

**us-central1 (primary orchestration region)**
- fsi-rm-a2a-gateway (Cloud Run)
- Vertex AI Reasoning Engine (Agent Engine)

**us-east1 (MCP and voice services)**
- fsi-rm-compliance-mcp (Cloud Run, min-instances=1)
- fsi-rm-core-banking-mcp (Cloud Run, min-instances=1)
- fsi-rm-portfolio-mcp (Cloud Run, min-instances=1)
- fsi-rm-comms-mcp (Cloud Run, min-instances=1)
- fsi-rm-voice-mcp (Cloud Run, min-instances=1)
- fsi-rm-liveapi-broker (Cloud Run, min-instances=1, max-instances=1)

The LiveAPI Broker is deliberately constrained to max-instances=1. Gemini Live API sessions are stateful WebSocket connections; multiple broker instances would break session continuity. This makes the broker a singleton in production.

### 6.2 AWS Region Layout

**us-east-1 (Bedrock AgentCore)**
- AMFI NAV Agent
- Market Data Agent (BSE/NSE)
- Credit Bureau Agent (CIBIL/Experian)
- Account Aggregator Agent (RBI AA Framework)

### 6.3 Scaling Policy

| Service | Min Instances | Max Instances | Rationale |
|---|---|---|---|
| a2a-gateway | 1 | auto | No cold starts; scales with RM load |
| compliance-mcp | 1 | auto | No cold starts |
| core-banking-mcp | 1 | auto | No cold starts |
| portfolio-mcp | 1 | auto | No cold starts |
| comms-mcp | 1 | auto | No cold starts |
| voice-mcp | 1 | auto | No cold starts |
| liveapi-broker | 1 | 1 | Singleton — stateful WebSocket sessions |

---

## 7. Security Model

### 7.1 Authentication Layers

The system uses distinct authentication mechanisms at each protocol boundary, chosen to match the security requirements of that boundary:

**Gemini Enterprise → Gateway (A2A)**
Gemini Enterprise passes a session context identifier (`contextId`) in the A2A JSON-RPC request. The Gateway validates the session and routes to the correct Reasoning Engine session. The connection is over Google-managed TLS.

**Gateway → Agent Engine (A2A)**
The Gateway uses a GCP service account with `aiplatform.reasoningEngines.query` IAM permission. Credentials are managed via Workload Identity on Cloud Run.

**Sub-agents → MCP Servers (MCP OAuth 2.1)**
Each sub-agent calls its MCP server using an impersonated service account token. The MCP invoker service account (`fsi-rm-mcp-invoker`) has:
- `serviceAccountTokenCreator` on itself (for self-impersonation)
- `run.invoker` at project level (to call Cloud Run endpoints)

This means each MCP call carries a short-lived OAuth 2.1 token scoped to the calling service account. MCP servers validate these tokens before executing any tool.

**GCP → AWS (SigV4)**
All calls from GCP agents to AWS Bedrock AgentCore endpoints are authenticated using AWS SigV4 signed requests. GCP service accounts that make these calls hold AWS IAM credentials stored in Secret Manager. SigV4 signatures are computed per-request with short TTLs.

**Twilio Webhook Authentication**
Twilio webhook calls to the comms-mcp and voice-mcp are validated using Twilio's X-Twilio-Signature header, verified against the shared auth token stored in Secret Manager.

### 7.2 IAM Principle of Least Privilege

Each Cloud Run service runs under a dedicated service account with only the permissions it requires:

- MCP servers have read-only BigQuery access to their relevant tables
- comms-mcp has GCS write access to fsi-rm-voice-notes only
- voice-mcp has GCS write access to fsi-rm-call-transcripts only
- No MCP server has IAM permissions outside its domain

### 7.3 Secret Management

All credentials (Twilio auth tokens, AWS access keys, API keys) are stored in GCP Secret Manager. Cloud Run services access secrets via mounted secret volumes or environment variable injection at runtime. No secrets are embedded in container images or source code.

### 7.4 Human-in-the-Loop as a Security Control

The RM approval requirement for all outbound actions is both a UX feature and a security control. It prevents:
- Agent hallucination from triggering client communications
- Prompt injection attacks that attempt to send unauthorized messages
- Automated escalation without RM awareness

### 7.5 Server-Side Script Locking as a Compliance Control

Voice note and communication scripts are stored server-side, referenced by `note_id`. The agent cannot pass arbitrary text to the TTS engine. This ensures:
- Only compliance-approved language is used in client communications
- The LLM cannot be prompted to alter approved scripts
- Every voice note sent corresponds to an auditable, versioned script

---

## 8. Data Flows

### 8.1 Standard Query Flow (e.g., "Show me Amit Joshi's portfolio")

```
RM types prompt in Gemini Enterprise
        │
        │  A2A JSON-RPC 2.0 (contextId: session ID)
        ▼
fsi-rm-a2a-gateway
        │  Routes to Reasoning Engine session
        │  A2A JSON-RPC 2.0
        ▼
Orchestrator Agent (Vertex AI Reasoning Engine)
        │  PreloadMemoryTool fires → loads client/RM memory context
        │  Intent classified: portfolio query
        │  Delegates to portfolio_agent
        ▼
portfolio_agent
        │  MCP call → fsi-rm-portfolio-mcp (StreamableHTTP, OAuth 2.1)
        │  MCP call → fsi-rm-core-banking-mcp (for account balances)
        │  A2A call → AWS AMFI Agent (SigV4) → current NAV data
        │  A2A call → AWS Market Data Agent (SigV4) → BSE/NSE prices
        ▼
portfolio_agent aggregates holdings, computes current values
        │
        ▼
Orchestrator Agent synthesizes response
        │  A2A JSON-RPC 2.0 response
        ▼
fsi-rm-a2a-gateway → Gemini Enterprise → RM sees formatted portfolio view
```

### 8.2 WhatsApp Voice Note Flow (e.g., "Send Amit a SIP reminder in Hindi")

```
RM approves sending SIP reminder
        │
        ▼
Orchestrator Agent → comms_agent
        │
        ▼
comms_agent
        │  MCP call → fsi-rm-comms-mcp
        │  Tool: send_whatsapp_voice_note(client_id=C0022, note_id="sip_reminder_hi_001")
        ▼
fsi-rm-comms-mcp
        │  Resolves note_id → approved Hindi script (server-side)
        │  Calls Gemini 3.1 Flash TTS → generates OGG/Opus audio
        │  Uploads audio → GCS fsi-rm-voice-notes/
        │  Calls Twilio WhatsApp Business API with GCS audio URL
        ▼
Twilio delivers voice note to client's WhatsApp
        │
        ▼
Confirmation returned up call stack → RM sees "Voice note sent to Amit Joshi"
```

### 8.3 Outbound Voice Call Flow (e.g., "Call Amit to discuss his SIP")

```
RM approves outbound call
        │
        ▼
Orchestrator Agent → voice_agent
        │
        ▼
voice_agent
        │  MCP call → fsi-rm-voice-mcp
        │  Tool: initiate_call(client_id=C0022, phone="+91XXXXXXXXXX")
        ▼
fsi-rm-voice-mcp → Twilio REST API
        │  Twilio dials client, connects Media Streams WebSocket
        │  WebSocket endpoint: fsi-rm-liveapi-broker
        ▼
fsi-rm-liveapi-broker (Cloud Run, max-instances=1)
        │  Accepts Twilio Media Streams WebSocket (mulaw 8kHz inbound)
        │  Opens WebSocket to Gemini Live API
        │  Bridges audio bidirectionally:
        │    Client audio (mulaw 8kHz) → Gemini Live (PCM 24kHz)
        │    Gemini Live response (PCM 24kHz) → Twilio (mulaw 8kHz)
        ▼
Real-time Hindi conversation between Gemini Live and client
        │
        │  On call disconnect:
        │  LiveAPI Broker flushes transcript buffer (flush-at-disconnect fix)
        │  Saves transcript → GCS fsi-rm-call-transcripts/
        ▼
Confirmation + transcript reference returned → RM sees call summary
```

### 8.4 Compliance Check Flow (e.g., "Is Amit's KYC current?")

```
RM asks compliance question
        │
        ▼
Orchestrator Agent → compliance_agent
        │
        ▼
compliance_agent
        │  MCP call → fsi-rm-compliance-mcp
        │  Query: BigQuery kyc_documents WHERE client_id = 'C0022'
        │  A2A call → AWS Credit Bureau Agent (SigV4)
        │    → CIBIL score, Experian report for client
        ▼
compliance_agent aggregates KYC status + credit data
        │
        ▼
Orchestrator synthesizes compliance summary → RM
```

---

## 9. Compliance and Governance

### 9.1 Regulatory Alignment

The system operates within the Indian financial services regulatory environment:

- **RBI Account Aggregator Framework:** Cross-bank data is retrieved only via the AA Framework through the AWS Account Aggregator Agent, ensuring consent-based data access.
- **KYC/AML:** KYC document status and expiry are stored in BigQuery and checked before any product recommendation or communication.
- **CIBIL/Experian:** Credit bureau data is accessed via the AWS Credit Bureau Agent, which operates under licensed bureau agreements.
- **SEBI/AMFI:** Mutual fund NAV data is sourced from AMFI India via the AWS AMFI Agent, ensuring official and timestamped pricing.

### 9.2 Audit Trail

Every agent action produces an auditable trace:

- Vertex AI Reasoning Engine logs all agent reasoning steps and tool calls
- MCP server call logs are persisted in Cloud Logging
- All outbound communications (WhatsApp, calls) are logged with timestamps, client IDs, and RM IDs
- Call transcripts are stored immutably in GCS fsi-rm-call-transcripts with object versioning
- BigQuery audit logs capture all data reads

### 9.3 Data Residency

Client financial data (accounts, holdings, KYC, CRM) resides in BigQuery in GCP. The AWS Bedrock agents consume external data feeds (AMFI, BSE/NSE, CIBIL, AA) but do not store client records. This separation ensures the system of record for client data remains within the GCP environment and under the bank's direct control.

### 9.4 Human Oversight

The system is designed to keep the RM in control of all consequential decisions:

- Agents can retrieve, analyze, and recommend — they cannot act on clients without RM approval
- The Orchestrator surfaces proposed actions explicitly, requiring affirmative RM confirmation
- There is no automated scheduling or background agent execution — all workflows are RM-initiated

---

## 10. Key Design Decisions

### Decision 1: Vertex AI Reasoning Engine over Custom Orchestration

**Choice:** Host the Orchestrator and all sub-agents in Vertex AI Reasoning Engine rather than building a custom orchestration framework.

**Rationale:** Reasoning Engine provides managed session state, built-in ADK support, automatic reasoning traces, and Vertex AI IAM integration. The alternative — a custom Cloud Run orchestrator — would require implementing session management, agent state persistence, and reasoning trace logging from scratch.

**Trade-off:** Less control over the underlying infrastructure; Vertex AI Reasoning Engine pricing scales with usage.

### Decision 2: Five Specialist Sub-agents Instead of One Large Agent

**Choice:** Route all intent through a specialist hierarchy rather than one large general-purpose agent.

**Rationale:** A single agent with access to all tools produces worse results at domain-specific tasks and is harder to debug. Specialist agents have smaller tool surfaces, clearer system prompts, and more focused context windows. The Orchestrator's intent-routing LLM call adds one hop of latency, but the quality improvement is significant.

**Trade-off:** Routing errors are possible (Orchestrator sends a query to the wrong sub-agent). Mitigated by clear intent classification in the Orchestrator system prompt.

### Decision 3: MCP over Direct API Calls from Agents

**Choice:** All tool execution goes through MCP servers; agents never call external APIs directly.

**Rationale:** MCP creates a clean separation between agent reasoning (what to do) and tool execution (how to do it). MCP servers handle authentication, retries, input validation, and logging independently of agent logic. This makes tools testable and replaceable without redeploying agents.

**Trade-off:** Additional network hop (agent → MCP server → API). Mitigated by min-instances=1 on all MCP servers eliminating cold-start latency.

### Decision 4: AWS Bedrock AgentCore for Regulated Data Agents

**Choice:** Run AMFI, Market Data, Credit Bureau, and AA agents on AWS Bedrock AgentCore rather than porting them to GCP.

**Rationale:** These agents consume data from Indian financial infrastructure (AMFI APIs, BSE/NSE data feeds, credit bureau APIs, AA framework APIs). Bedrock AgentCore provides managed agent hosting with AWS-native IAM, and these data sources have existing AWS integrations. Cross-cloud A2A calls with SigV4 authentication provide a secure bridge.

**Trade-off:** Operational complexity of managing two cloud providers; cross-cloud latency adds 50-150ms per call. Acceptable given that these calls are data lookups, not real-time voice.

### Decision 5: LiveAPI Broker as Singleton

**Choice:** fsi-rm-liveapi-broker configured with max-instances=1.

**Rationale:** Gemini Live API sessions are stateful WebSocket connections. A Twilio call must maintain a persistent WebSocket to the same broker instance for the duration of the call. Cloud Run's load balancer does not guarantee session affinity across multiple instances, so multiple instances would break active calls.

**Trade-off:** The singleton broker is a single point of failure for voice calls. Mitigated by Cloud Run's managed health checks and automatic restart on failure. This is an acknowledged limitation of the current architecture; a production-scale voice system would require a different approach (e.g., a stateful session router with sticky routing).

### Decision 6: note_id Pattern for Voice Notes

**Choice:** Agents reference voice note scripts by `note_id` rather than passing free-form text to TTS.

**Rationale:** Financial communications must use compliance-approved language. An LLM generating ad-hoc audio scripts introduces hallucination risk into client-facing communications. Server-side script locking ensures only approved scripts are delivered. It also enables version control and audit of every script variant.

**Trade-off:** Less flexibility for RM-customized messages. The trade-off is intentional and explicit: compliance safety over personalization for voice notes.

### Decision 7: Session Continuity via contextId

**Choice:** Gemini Enterprise sends session ID as `params.message.contextId` (not `params.sessionId`), and the Gateway routes on this field.

**Rationale:** Gemini Enterprise's A2A implementation populates `contextId` in the message object rather than the standard `sessionId` field. The Gateway was updated to read from `params.message.contextId` to maintain session continuity across multi-turn RM conversations. Without this fix, each RM message would start a new Agent Engine session, losing all conversational context.

### Decision 8: 24kHz Audio Pipeline

**Choice:** Gemini Live API output is treated as 24kHz PCM throughout the LiveAPI Broker.

**Rationale:** Gemini Live outputs PCM audio at 24kHz. Early versions of the broker used 16kHz as the sample rate, causing audio to play at approximately 67% of normal speed (speech sounded slowed down and distorted). The broker now correctly uses 24000 Hz in all rate conversion operations.

---

## 11. Known Limitations and Roadmap

### Current Known Issues

**Client Name in Greeting:** The voice agent occasionally greets the client using the RM's name rather than the client's name in its opening line. This is a context variable ordering issue in the voice agent's system prompt, identified in the May 2026 demo checkpoint.

**en-IN Language Tag:** A bug with the `en-IN` language tag in certain Twilio TTS fallback paths produces unexpected behavior. The primary Hindi voice path (Gemini Live) is unaffected; this impacts only the English TTS fallback route.

**LiveAPI Broker Singleton Scalability:** The single-instance constraint on the LiveAPI Broker means only one concurrent voice call is supported. This is acceptable for the current RM-count in production but would need architectural revision for wider deployment.

### Roadmap Considerations

- **Multi-RM Scaling:** The current system is optimized for a small number of RMs. Scaling to 50+ RMs would require revisiting the LiveAPI Broker singleton constraint and potentially introducing a session router layer.
- **Inbound Call Handling:** The current voice pipeline is outbound-only. Inbound call routing from clients to their RM's AI agent is a future capability.
- **Email Channel:** The comms_agent is designed to support email dispatch but this channel is not yet activated in production.
- **Agent Evaluation:** Systematic evaluation of agent response quality using ADK eval tooling is planned to measure and improve response accuracy across the five sub-agent domains.

---

*This document describes the production state of the system as of 2026-05-29. All component IDs, service names, and architectural decisions reflect the deployed configuration.*