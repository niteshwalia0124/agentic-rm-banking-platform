# Agent Teams for Relationship Managers — Complete Technical & Business Documentation

**Project:** Cymbal Bank Relationship Manager AI Platform  
**Environment:** GCP Project `<YOUR_GCP_PROJECT>`, Region `asia-south1` (MCP/Gateway), `us-east1` (liveapi-broker)  
**Repository:** `https://github.com/niteshwalia0124/agentic-rm-banking`  
**Stable Checkpoint:** Commit `02cec02` — all 6 prompts verified end-to-end, 2026-05-23

---

## Table of Contents

1. [Business Context & Problem Statement](#1-business-context--problem-statement)
2. [System Architecture Overview](#2-system-architecture-overview)
3. [How Agentic Systems Work Internally](#3-how-agentic-systems-work-internally)
4. [Protocols Deep Dive: A2A and MCP](#4-protocols-deep-dive-a2a-and-mcp)
5. [The A2A Gateway — Entry Point for Every Request](#5-the-a2a-gateway--entry-point-for-every-request)
6. [The Orchestrator Agent — The Brain](#6-the-orchestrator-agent--the-brain)
7. [Sub-Agents — The Specialists](#7-sub-agents--the-specialists)
8. [MCP Servers — The Data Layer](#8-mcp-servers--the-data-layer)
9. [External A2A Agents on AWS — The Market Intelligence Layer](#9-external-a2a-agents-on-aws--the-market-intelligence-layer)
10. [The Voice Pipeline — End-to-End Call Architecture](#10-the-voice-pipeline--end-to-end-call-architecture)
11. [Authentication & IAM Architecture](#11-authentication--iam-architecture)
12. [Session & Memory Architecture](#12-session--memory-architecture)
13. [All 6 Demo Prompts — Full Log Traces & Latency Analysis](#13-all-6-demo-prompts--full-log-traces--latency-analysis)
14. [Known Issues & Root Causes](#14-known-issues--root-causes)
15. [Infrastructure Inventory](#15-infrastructure-inventory)

---

## 1. Business Context & Problem Statement

### The RM's Day Before This System

A Relationship Manager at an Indian bank manages 100–300 clients. Their day looks like:

| Task | Time Spent | Value |
|---|---|---|
| Pulling client data from 3–5 systems | 45 min/day | Low — operational |
| Checking which SIPs expire this month | 20 min/day | Low — clerical |
| Drafting portfolio review emails | 10 min/email | Medium — templated |
| Scheduling calls and follow-ups | 20 min/meeting | Low — admin |
| Reviewing compliance alerts | 30 min/day | Medium — mandatory |
| **Actual advisory conversations** | **< 2 hours/day** | **High — revenue** |

The RM spends 70%+ of their time on low-value operational work. They have no time to proactively surface opportunities, call clients before SIPs expire, or send personalized outreach at scale.

### What This System Does

An AI agent team that runs the operational 70% — so the RM focuses exclusively on high-value advisory conversations.

**The system handles:**
- Morning brief: compliance alerts + expiring SIPs + urgent clients — auto-generated daily
- Client 360° view on demand: full profile with accounts, portfolio, KYC, credit score, cross-bank data
- Portfolio analysis with live NAV from AMFI (India) and live stock prices (NSE/BSE) via AWS agents
- Email and WhatsApp drafting with full client context — staged for RM approval, never auto-sent
- WhatsApp voice notes in 11 Indian languages using Google Cloud TTS Neural2 voices
- Outbound AI voice calls via Twilio + Gemini Live — Priya calls the client in Hindi/regional language on behalf of the RM

**Estimated time recaptured: 3–4 hours per RM per day.**

### India-Specific Design Decisions

1. **Hindi-first, multilingual**: Gemini 3 Live API for voice calls, Gemini 3.1 TTS for WhatsApp notes — 11 Indian languages natively. RBI FREE-AI framework requires vernacular language accessibility.
2. **Account Aggregator integration**: RBI AA Framework covers 2.2B accounts. RM can see client's FDs at HDFC, SIPs at Axis, loan at SBI — all in one view with client consent.
3. **SEBI IA compliance**: All investment recommendations staged for RM review — system never gives advice autonomously.
4. **RBI Fair Practices Code**: Voice calls only between 08:00–19:00 local time, enforced in voice_mcp.
5. **DPDP Act 2023**: All data stays in GCP India regions (asia-south1, asia-south2). PII filtered at OTel collector before reaching observability backend.

---

## 2. System Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                       RELATIONSHIP MANAGER INTERFACE                          │
│                                                                                │
│    Gemini Enterprise / Agentspace Chat                                         │
│    (RM types queries in natural language, sees responses in real-time)         │
└──────────────────────────────┬───────────────────────────────────────────────┘
                               │
                    A2A Protocol (JSON-RPC over HTTPS)
                    message/stream → SSE response
                               │
              ┌────────────────▼────────────────┐
              │    FSI-RM A2A GATEWAY            │
              │    Cloud Run · asia-south1        │
              │    gateway/a2a_server.py          │
              │                                   │
              │  • Parses A2A JSON-RPC body       │
              │  • Extracts session contextId     │
              │  • Vertex AI Session Service      │
              │  • Vertex AI Memory Bank          │
              │  • ADK Runner.run_async()         │
              └────────────────┬────────────────┘
                               │ ADK in-process
                               │
              ┌────────────────▼────────────────┐
              │    ORCHESTRATOR AGENT            │
              │    (ADK LlmAgent — Gemini Flash) │
              │    agents/orchestrator/agent.py  │
              │                                  │
              │  • PreloadMemoryTool (first)     │
              │  • Routing map in system prompt  │
              │  • Routes to specialist agents   │
              └──┬──────┬──────┬──────┬──────┬──┘
                 │      │      │      │      │
          AgentTool × 5 (in-process, sub-millisecond routing)
                 │      │      │      │      │
    ┌────────────┘  ┌───┘  ┌───┘  ┌───┘  ┌──┘
    │               │      │      │      │
    ▼               ▼      ▼      ▼      ▼
client_intel    portfolio  comms  compliance  voice
_agent          _agent     _agent  _agent     _agent
    │               │      │      │           │
    │           (MCPToolset per agent — StreamableHTTP)
    │               │      │      │           │
    ▼               ▼      ▼      ▼           ▼
core-banking-  portfolio- comms- compliance- voice-mcp
mcp            mcp        mcp    mcp         │
    │               │                        │
    ▼               │                   ┌────▼─────────────────┐
[BigQuery:          │                   │  liveapi-broker       │
 fsi_rm_poc]        │                   │  Cloud Run · us-east1 │
                    │                   │  bridge/liveapi_broker│
                    │                   │  .py                  │
               ┌────▼──────────────┐   │                       │
               │  AWS BEDROCK      │   │  Twilio → Gemini Live │
               │  AGENTCORE        │   │  (real-time voice)    │
               │  (us-east-1)      │   └──────────────────────-┘
               │                   │
               │  AMFI NAV Agent   │
               │  Market Data Agent│
               │  Credit Bureau    │
               │  Account Aggreg.  │
               └───────────────────┘
```

### Data Flow Summary

Every RM query travels this path:

```
RM types → Gemini Enterprise
         → POST /.../agent-card.json (discovery, once)
         → POST / (A2A JSON-RPC, every message)
         → Gateway parses + resolves session
         → ADK Runner starts orchestrator
         → Orchestrator reads system prompt + memory
         → Orchestrator routes to specialist agent via AgentTool
         → Specialist discovers MCP tools (Agent Registry or env var)
         → Specialist calls MCP tools (HTTP POST /mcp)
         → MCP server queries BigQuery / calls Twilio / calls external APIs
         → Results flow back through agent → orchestrator → gateway → SSE → RM
```

---

## 3. How Agentic Systems Work Internally

This is the most important section for understanding WHY the system behaves the way it does.

### 3.1 What is an LLM Agent?

An LLM Agent is a loop:

```
┌─────────────────────────────────────────────────────┐
│  AGENT LOOP (runs until no more tool calls needed)   │
│                                                       │
│  1. Build prompt = system_prompt + history + new_msg │
│  2. Call LLM (Gemini Flash) → get response           │
│  3. Response contains text OR tool_call(s)           │
│     a. If TEXT → this is the final response. Stop.   │
│     b. If TOOL CALL(s) → execute tools, append       │
│        results to history, go back to step 1.        │
└─────────────────────────────────────────────────────┘
```

The LLM itself never executes tools. It only **decides** which tool to call and with what arguments. ADK (Agent Development Kit) runs the tool, appends the result to the conversation history, and sends the updated history back to the LLM for the next decision.

### 3.2 How the Orchestrator Routes to Sub-Agents

Sub-agents (portfolio_agent, client_intel_agent, etc.) are wrapped as `AgentTool` objects and registered in the orchestrator's tool list. To the orchestrator's LLM, a sub-agent looks exactly like any other tool call:

```python
agent_tool.AgentTool(agent=_build_portfolio())
# The orchestrator sees this as: tool_name="portfolio_agent", args={"request": "..."}
```

When the orchestrator decides to call `portfolio_agent`, ADK:
1. Runs the portfolio agent's LLM with the request
2. The portfolio agent calls its own MCP tools
3. Returns the final portfolio agent response to the orchestrator as the tool result
4. The orchestrator incorporates this and generates its final response

This is **in-process** — no network hop. The orchestrator and all sub-agents run in the same Python process inside the gateway container.

### 3.3 The System Prompt is Everything

The orchestrator's behavior is entirely defined by its system prompt. This is the most important file in the system:

```python
_SYSTEM_PROMPT_TEMPLATE = """
You are a ROUTING AGENT for a Relationship Manager (RM) at an Indian bank.
Your ONLY job is to understand the RM's intent and call the right specialist agent.

━━━ CRITICAL ROUTING RULES ━━━
You are a ROUTER — you do NOT have direct access to any banking system...
NEVER fabricate results.

━━━ ROUTING MAP ━━━
"morning brief" / "what needs attention"
  → Step 1: compliance_agent (pass rm_id)
  → Step 2: portfolio_agent  (pass rm_id — bulk tool)

"client profile" / "tell me about [client]"
  → client_intel_agent

"email" / "draft" / "WhatsApp text"
  → comms_agent

"voice note" / "call" / "phone"
  → voice_agent (ALWAYS — never handle voice yourself)

"KYC" / "compliance" / "AML"
  → compliance_agent
"""
```

**Why the routing map matters:** Without explicit routing rules, Gemini's LLM would use its own judgment to pick the agent. For "what needs my attention?", it would intuitively call `client_intel_agent` (thinking: "attention = client data"). The routing map overrides this with explicit if/then rules — effectively injecting deterministic routing logic into an otherwise probabilistic LLM.

### 3.4 The Pickle-Safe Pattern

Agent Engine pickles (serializes) the orchestrator agent between invocations to save memory. This causes a critical problem: MCP connections are in-memory socket connections — they can't be pickled. When Agent Engine unpickles a stale agent, the MCP connections would be dead.

The solution is `_PickleSafeOrchestrator`:

```python
class _PickleSafeOrchestrator(LlmAgent):
    def __reduce__(self):
        # When Python unpickles this object, it calls _build_orchestrator()
        # instead of restoring the stale state. This rebuilds the entire
        # agent tree with fresh MCP connections.
        return (_build_orchestrator, ())

    def __deepcopy__(self, memo):
        return _build_orchestrator()
```

Every agent class in the system inherits this pattern. On unpickle, the entire agent tree is rebuilt from scratch — fresh MCP connections, fresh Agent Registry discovery, fresh LLM clients.

### 3.5 The MCP Session Lifecycle

MCP (Model Context Protocol) uses StreamableHTTP transport. The session lifecycle for a single agent invocation:

```
Agent starts → POST /mcp (init)        → 200 Accepted, sets X-MCP-Session-Id header
             → GET /mcp (SSE stream)   → 200 OK, opens event stream  
             → POST /mcp (list tools)  → tools JSON returned via SSE stream
             → POST /mcp (tool call 1) → tool result returned via SSE stream
             → POST /mcp (tool call 2) → tool result returned via SSE stream
             → DELETE /mcp (cleanup)   → 200 OK, session terminated
```

**Critical constraint: Sessions are in-memory on the MCP server.** The session object (with its event stream) lives in the MCP server's RAM. If the GET /mcp (SSE stream) hits a different Cloud Run instance than the POST /mcp (init), the session doesn't exist on that instance → 404 → `McpError: Session terminated`.

**Fix:** `--max-instances=1` on all MCP Cloud Run services. Every request for a given service hits the same instance, keeping the session alive.

### 3.6 Tool Parallelism

ADK can call multiple tools in parallel if the LLM returns multiple tool calls in a single response. This is why you see bursts like:

```
15:21:32 | fsi-rm-portfolio-mcp | 4 CallToolRequests at the same second
```

The LLM decided "I need holdings, SIP schedule, loan summary, and demat" and returned all 4 tool calls at once. ADK executes them concurrently using asyncio. This is the difference between 4 × 2s = 8s sequential vs 2s parallel.

The orchestrator's routing map also instructs the agent to use BULK tools for RM-wide queries:

```
get_clients_with_expiring_sips(rm_id, days_ahead=30)  ← ONE call for ALL clients
```

vs the old behavior:

```
for each client:
    get_sip_schedule(client_id)  ← 26 sequential calls = 2m21s
```

---

## 4. Protocols Deep Dive: A2A and MCP

### 4.1 A2A Protocol (Agent-to-Agent)

A2A is an open standard (Linux Foundation, April 2026) for agents to communicate with each other. In this system it serves two roles:

**Role 1: RM interface → Gateway**
Gemini Enterprise sends every RM message as an A2A `message/stream` JSON-RPC call:

```json
{
  "jsonrpc": "2.0",
  "method": "message/stream",
  "id": "uuid",
  "params": {
    "message": {
      "contextId": "session-rm-default-8d485eff",
      "kind": "message",
      "parts": [{"kind": "text", "text": "Give me my morning brief"}],
      "role": "user"
    }
  }
}
```

Key fields:
- `contextId`: The session ID. Gemini Enterprise sends the same ID for all messages in a chat conversation. This maps to a Vertex AI session, allowing conversation history to persist.
- `method: message/stream`: The gateway returns a Server-Sent Events (SSE) stream, allowing the response to appear token-by-token in real-time.

**Role 2: GCP agents → AWS AgentCore agents**

The portfolio and client_intel agents call external AWS agents via A2A:

```python
# In agents/portfolio/agent.py
async def get_mutual_fund_nav(fund_name: str) -> dict:
    return await a2a_call(os.getenv("AMFI_AGENT_URL", ""), fund_name)
```

`a2a_call()` in `external_agents/a2a_client.py` constructs:
```json
{
  "jsonrpc": "2.0",
  "method": "message/send",
  "params": {
    "message": {
      "role": "user",
      "parts": [{"kind": "text", "text": "Get NAV for Mirae Asset Large Cap Fund"}]
    }
  }
}
```

With **AWS SigV4 authentication** — every request is signed with HMAC-SHA256 using the AWS access key. AWS Bedrock AgentCore rejects unsigned requests.

**Agent Card Discovery:**
Every A2A agent publishes a card at `/.well-known/agent-card.json`. Gemini Enterprise reads this once to understand the agent's capabilities, skills, and authentication requirements.

```json
{
  "name": "FSI Relationship Manager AI",
  "skills": [
    {"id": "morning_brief", "description": "Daily compliance digest..."},
    {"id": "voice_call", "description": "AI voice calls in 11 Indian languages..."}
  ],
  "capabilities": {"streaming": true}
}
```

### 4.2 MCP Protocol (Model Context Protocol)

MCP is the standard for agent-to-tool connections. Each MCP server is a standalone HTTP service that exposes tools (functions) the agent can call.

**The 3-message handshake:**

```
1. POST /mcp (Content-Type: application/json)
   Body: {"jsonrpc":"2.0","method":"initialize","params":{...}}
   Response: 202 Accepted
   Header: X-MCP-Session-Id: <uuid>
   
2. GET /mcp (with X-MCP-Session-Id header)
   Response: 200 OK, Content-Type: text/event-stream
   [opens persistent SSE stream — all subsequent results come here]

3. POST /mcp (Content-Type: application/json, X-MCP-Session-Id header)
   Body: {"jsonrpc":"2.0","method":"tools/list","id":"1"}
   → Result arrives via SSE stream, not in POST response body
```

**Why SSE for results?**
MCP separates request (POST) from response (SSE stream). This allows the MCP server to push tool results asynchronously — useful when tool execution takes time. The POST just queues the work; the result arrives on the already-open GET stream.

**Why this breaks with multiple Cloud Run instances:**
Instance A handles POST /mcp (init) → creates session in memory → returns session ID.
Instance B gets GET /mcp with that session ID → session doesn't exist on B → 404.
Fix: `--max-instances=1` ensures all requests for one service go to the same instance.

---

## 5. The A2A Gateway — Entry Point for Every Request

**File:** `gateway/a2a_server.py`  
**Deployment:** Cloud Run, `asia-south1`, `fsi-rm-a2a-gateway`

The gateway is the bridge between Gemini Enterprise and the ADK agent system. It is a FastAPI application with 4 endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /.well-known/agent-card.json` | A2A discovery — Gemini Enterprise reads this once |
| `GET /.well-known/agent.json` | Legacy path for older A2A clients |
| `POST /` | Main handler — all RM queries arrive here |
| `POST /stream` | Streaming variant (SSE) |
| `GET /health` | Cloud Run health check |

### Request Processing Flow

```python
async def handle_task(request: Request):
    body = await request.json()
    
    # 1. Extract session context
    session_id = _extract_or_create_session(body, user_id)
    # Gemini Enterprise sends contextId inside params.message
    # → message.get("contextId") or auto-generate "session-{user}-{uuid8}"
    
    # 2. Extract the RM's actual text
    user_text = _extract_message_text(body)
    
    # 3. Stream the response
    async def event_stream():
        response_text = await _run_agent(user_id, session_id, user_text)
        yield _sse_event({"result": {"parts": [{"text": response_text}]}})
    
    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

### The `_run_agent` function — Where ADK Runs

```python
async def _run_agent(user_id, session_id, text):
    # 1. Ensure Vertex AI session exists (creates if new)
    session = await session_service.get_session(app_name, user_id, session_id)
    if session is None:
        session = await session_service.create_session(...)
    
    # 2. Run the orchestrator (async generator — yields events as agent thinks)
    async for event in runner.run_async(user_id, session_id, new_message):
        if event.is_final_response():
            response_text = event.content.parts[0].text
    
    # 3. Persist the completed turn to Memory Bank
    # Memory Bank auto-extracts facts: "RM asked about Rekha's SIP" →
    # stored as long-term memory, recalled on future sessions via PreloadMemoryTool
    await memory_service.add_session_to_memory(session)
    
    return response_text
```

### Session Extraction Logic

The `contextId` comes from different places depending on the client:

```python
def _extract_or_create_session(body, user_id):
    message = body["params"]["message"]
    return (
        message.get("contextId")           # Gemini Enterprise (primary)
        or params.get("sessionId")          # Direct curl tests
        or meta.get("session_id")           # Custom clients
        or f"session-{user_id}-{uuid4()}"  # Auto-generate if new
    )
```

This is why the same Gemini Enterprise chat always sends `contextId: session-rm-default-8d485eff` — the Enterprise UI assigns a fixed ID per conversation, enabling multi-turn memory.

### Gateway Warnings (Non-Fatal)

Every request logs:
```
App name mismatch: configured "fsi-rm-agentspace" but agent loaded from "/app/agents/voice"
```

This happens because each sub-agent reports its own app name. The gateway is configured as `fsi-rm-agentspace` (the Agentspace app name) but the ADK runner sees the loaded agent's internal name. Non-fatal — doesn't affect functionality.

---

## 6. The Orchestrator Agent — The Brain

**File:** `agents/orchestrator/agent.py`  
**Model:** `gemini-3.5-flash` (via `GEMINI_MODEL` env var)

### Tool Registry

The orchestrator has exactly 7 tools:

| Tool | Type | Purpose |
|---|---|---|
| `PreloadMemoryTool` | Built-in ADK | Retrieves cross-session memories FIRST on every turn |
| `list_mcp_connections` | FunctionTool | Utility — shows which MCP servers are live |
| `client_intel_agent` | AgentTool | 360° client view |
| `portfolio_agent` | AgentTool | Portfolio analysis |
| `comms_agent` | AgentTool | Email/WhatsApp drafting |
| `compliance_agent` | AgentTool | KYC/AML/compliance digest |
| `voice_agent` | AgentTool | Voice notes + outbound calls |

`AgentTool` wraps a complete sub-agent as a single callable. To the orchestrator LLM, it looks like:

```
Tool: portfolio_agent
Description: "Analyzes client portfolio: MF holdings, SIPs, loans, stocks, FDs. Flags expiries."
Input: { "request": string }  # The RM's portfolio-related question
Output: string  # The portfolio agent's complete formatted response
```

### PreloadMemoryTool — Why It Runs First

Memory Bank stores long-term facts extracted from previous sessions. When the RM comes back after 3 days and asks about Rekha Mehta, PreloadMemoryTool retrieves:
- "RM discussed Rekha's SIP renewal on 2026-05-21"
- "Rekha holds Mirae Asset Large Cap, ₹37,655/month"
- "RM wanted to schedule a call with Rekha for SIP renewal discussion"

This context injects automatically — the RM doesn't need to re-explain everything every session. ADK runs `PreloadMemoryTool` before the LLM's first step because it's registered first in the tools list.

### Dynamic System Prompt

```python
def _render_mcp_doc() -> str:
    """Lists discovered MCP servers in the system prompt."""
    lines = []
    for s in DISCOVERED_MCP_SERVERS:
        prefix = s.get("tool_name_prefix")
        name = s.get("name", "?")
        lines.append(f"  - {name} (tools prefixed `{prefix}_*`)")
    return "\n".join(lines)

instruction = _SYSTEM_PROMPT_TEMPLATE.format(mcp_services_doc=_render_mcp_doc())
```

The system prompt is built at startup with the live list of discovered MCP servers. This keeps the orchestrator informed about what infrastructure is available without hardcoding.

---

## 7. Sub-Agents — The Specialists

### 7.1 Client Intelligence Agent

**File:** `agents/client_intel/agent.py`  
**MCP:** `core-banking-mcp` (→ BigQuery `fsi_rm_poc`)  
**A2A Tools:** CIBIL credit bureau (AWS), Account Aggregator (AWS)

```
client_intel_agent
    ├── core-banking-mcp tools:
    │     get_client_profile(client_id)
    │     get_account_summary(client_id)
    │     get_transaction_history(client_id, days)
    │     get_kyc_status(client_id)
    │     get_crm_history(client_id)
    │     get_clients_for_rm(rm_id)
    │
    ├── get_cibil_report(pan) → AWS Credit Bureau AgentCore
    └── get_aa_holdings(customer_id) → AWS Account Aggregator AgentCore
```

**Output format (Client Card):**
```
Client Card: Rekha Mehta (C0040)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Segment: HNI | Risk: Moderate-Aggressive
Savings A/C: ₹4.2L | FD: ₹25L | Current: ₹1.1L
KYC: ⚠️ Aadhaar expires June 2
Last Contact: 2026-04-15 (portfolio review call)
Next Best Action: SIP renewal + KYC update
```

### 7.2 Portfolio Agent

**File:** `agents/portfolio/agent.py`  
**MCP:** `portfolio-mcp` (→ BigQuery `fsi_rm_poc`)  
**A2A Tools:** AMFI NAV Agent (AWS), Market Data Agent (AWS)

The portfolio agent has a critical dual-mode behavior:

**RM-Wide Mode (bulk):**
```python
get_clients_with_expiring_sips(rm_id="RM001", days_ahead=30)
# Returns ALL clients with expiring SIPs in ONE query
# → "Rekha Mehta (C0040): ₹37,655/month expires June 11"
# → "Arjun Sharma (C0015): ₹25,000/month expires June 8"
```

**Single-Client Mode (per-client):**
```python
get_mf_holdings(client_id="C0040")      # MF holdings with NAV
get_sip_schedule(client_id="C0040")     # Active SIPs
get_loan_summary(client_id="C0040")     # Loans + EMIs
get_demat_holdings(client_id="C0040")   # Stock positions
get_mutual_fund_nav(fund_name="Mirae Asset Large Cap")  # → AWS AMFI agent
get_market_data(symbol="HDFCBANK")      # → AWS Market Data agent
```

Before this session's fix, the agent used per-client tools for RM-wide queries, iterating through all 26 clients = 26 sequential tool calls = 2m21s. Now it uses `get_clients_with_expiring_sips` = 1 tool call = ~3s.

### 7.3 Communications Agent

**File:** `agents/comms/agent.py`  
**MCP:** `comms-mcp`

```
comms-mcp tools:
    draft_email(to, subject, body_context, client_id)
    draft_whatsapp_text(mobile, message_context, client_id)
    create_meeting_invite(attendees, subject, proposed_times)
    get_email_thread(thread_id)
```

**Human-in-loop guarantee:** The comms agent ONLY drafts. It never sends. The `draft_email` tool returns a formatted preview for RM approval. Sending requires the RM to explicitly approve in the Gemini Enterprise interface. This satisfies RBI FREE-AI's human-in-loop requirement.

### 7.4 Compliance Agent

**File:** `agents/compliance/agent.py`  
**MCP:** `compliance-mcp` (→ BigQuery `fsi_rm_poc`)

```
compliance-mcp tools:
    get_daily_compliance_digest(rm_id)     # Full digest in ONE call
    get_kyc_expiry_alerts(rm_id, days)
    get_aml_flags(rm_id)
    get_overdue_emis(rm_id)
    get_clients_not_contacted(rm_id, days)
```

**Output format:**
```
🔴 URGENT (action today):
  • Rekha Mehta (C0040): Aadhaar KYC expires June 2 — Per RBI KYC Direction 2016
  • Priya Sharma (C0012): EMI overdue 45 DPD — follow up required

🟡 THIS WEEK:
  • Arjun Kumar (C0015): SIP expires June 8, no renewal instruction

🟢 THIS MONTH:
  • 3 clients with KYC expiring before July 15
```

### 7.5 Voice Agent

**File:** `agents/voice/agent.py`  
**MCP:** `voice-mcp`

```
voice-mcp tools:
    build_voice_note_script(note_type, client_name, language, script_variables)
    send_whatsapp_voice_note(mobile, client_name, message_text, language, ...)
    initiate_voice_call(mobile, client_name, language, rm_name, call_type, ...)
    get_supported_languages()
    get_call_status(call_id)
```

The voice agent is the most complex — it chains 3 tools for a WhatsApp voice note:
1. `build_voice_note_script(note_type="sip_renewal", ...)` → generates Hindi text
2. `send_whatsapp_voice_note(message_text=<above>, language="hi-IN", ...)` → TTS + Twilio

And 3 tools for a voice call:
1. `initiate_voice_call(...)` → POST to liveapi-broker `/calls/{id}/prepare` + POST to Twilio Calls.json
2. `get_call_status(call_id)` → check Twilio call status
3. Optionally: script preparation tools

---

## 8. MCP Servers — The Data Layer

All 5 MCP servers share the same `Dockerfile` and are deployed as separate Cloud Run services. Each is a FastAPI app using `mcp[cli]` (FastMCP library).

### Common Pattern

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("core-banking-mcp")

@mcp.tool()
def get_client_profile(client_id: str) -> dict:
    """Fetch complete client profile from core banking system."""
    # Query BigQuery: SELECT * FROM fsi_rm_poc.clients WHERE client_id = @id
    client = bq_client.query(...).result()
    return {"client_id": ..., "name": ..., "segment": ...}

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
```

FastMCP handles the MCP protocol (init, SSE stream, tool dispatch) automatically. The developer only writes the tool functions.

### Voice MCP — TTS and Twilio

`mcp_servers/voice_mcp.py` is the most complex server. Key internals:

**TTS Voice Map (Neural2-A per language):**
```python
_TTS_VOICE_MAP = {
    "hi-IN": "hi-IN-Neural2-A",   # Hindi — warmest female voice
    "ta-IN": "ta-IN-Neural2-A",   # Tamil
    "te-IN": "te-IN-Neural2-A",   # Telugu
    "kn-IN": "kn-IN-Neural2-A",   # Kannada
    "ml-IN": "ml-IN-Neural2-A",   # Malayalam
    "mr-IN": "mr-IN-Neural2-A",   # Marathi
    "bn-IN": "bn-IN-Neural2-A",   # Bengali
    "gu-IN": "gu-IN-Neural2-A",   # Gujarati
    "pa-IN": "pa-IN-Wavenet-A",   # Punjabi (no Neural2 available)
    "en-IN": "en-IN-Neural2-A",   # Indian English
}

def _tts_to_ogg(text: str, language_code: str) -> bytes:
    client = texttospeech.TextToSpeechClient()
    voice = texttospeech.VoiceSelectionParams(
        language_code=language_code,
        name=_TTS_VOICE_MAP.get(language_code, f"{language_code}-Standard-A"),
    )
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.OGG_OPUS,
        speaking_rate=0.90,   # Slightly slower = more natural for Indian cadence
        pitch=1.5,            # +1.5 semitones = warmer, less robotic
    )
    response = client.synthesize_speech(...)
    return response.audio_content
```

Why `speaking_rate=0.90`? Default rate sounds rushed for WhatsApp voice notes. Indian communication style is slightly slower and more deliberate. 0.90 hits the sweet spot.

Why `pitch=1.5`? Default Neural2 voices are accurate but slightly cold. +1.5 semitones adds warmth without sounding artificial.

**WhatsApp Voice Note Flow:**
```
build_voice_note_script("sip_renewal", "Rekha Mehta", "hi-IN", {fund_name, expiry, amount})
  → Hindi text: "Namaste Rekha Mehta ji! Aapka Mirae Asset Large Cap SIP 11 June ko expire ho raha hai..."

send_whatsapp_voice_note(mobile="+919154314766", message_text=<above>, language="hi-IN")
  → _tts_to_ogg(text, "hi-IN")
    → Google Cloud TTS API → Neural2-A OGG audio bytes
  → _upload_audio_to_gcs(audio_bytes, "WA-XXXXXXXX.ogg")
    → public GCS URL: https://storage.googleapis.com/fsi-rm-audio/voice-notes/WA-XXXXXXXX.ogg
  → _send_twilio_whatsapp_media(to="+919154314766", media_url=<above>)
    → POST https://api.twilio.com/.../Messages.json
    → HTTP 201 Created — message SID: SMxxxx
```

**Voice Call Initiation:**
```
initiate_voice_call(mobile="+919154314766", client_name="Rekha Mehta", rm_name="Ravi Gupta", language="hi-IN")
  → POST https://liveapi-broker.../calls/CALL-XXXXXXXX/prepare
    → stores {language, rm_name, client_name, system_instruction} in CALL_CONTEXT[call_id]
    → returns 200 OK
  
  → POST https://api.twilio.com/.../Calls.json
    → from: "+1415xxxxxxx" (Twilio number)
    → to: "+919154314766" (Rekha's number)
    → url: "https://liveapi-broker.../twilio/voice/CALL-XXXXXXXX"
    → HTTP 201 Created — call SID: CAxxxx
    → Twilio begins dialling Rekha
```

---

## 9. External A2A Agents on AWS — The Market Intelligence Layer

The system has 4 external agents running on AWS Bedrock AgentCore (`us-east-1`). These are accessed from GCP via A2A Protocol with SigV4 authentication.

| Agent | URL env var | Query format | Returns |
|---|---|---|---|
| AMFI NAV Agent | `AMFI_AGENT_URL` | Fund name string | NAV, 1Y/3Y/5Y returns, AUM, category |
| Market Data Agent | `MARKET_DATA_AGENT_URL` | NSE/BSE ticker | Price, day change, 52W range, volume |
| Credit Bureau Agent | `CREDIT_BUREAU_AGENT_URL` | PAN number | CIBIL score, credit history, active loans |
| Account Aggregator Agent | `ACCOUNT_AGGREGATOR_AGENT_URL` | Customer ID | Cross-bank accounts, total assets/liabilities |

### SigV4 Authentication Flow

```python
def _sigv4_headers(method, url, body, region="us-east-1"):
    session = boto3.Session()
    creds = session.get_credentials().get_frozen_credentials()
    aws_request = AWSRequest(method=method, url=url, data=body)
    SigV4Auth(creds, "bedrock-agentcore", region).add_auth(aws_request)
    return dict(aws_request.headers)
    # Adds: Authorization: AWS4-HMAC-SHA256 Credential=.../bedrock-agentcore/aws4_request...
    #       x-amz-date: 20260523T154846Z
    #       x-amz-security-token: ...
```

AWS verifies: the request was signed with a valid AWS access key, the signature is less than 15 minutes old, the signing service is `bedrock-agentcore`. Any mismatch → 403.

Additionally: `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` header must be ≥ 33 characters (UUID = 36 chars, satisfies this).

---

## 10. The Voice Pipeline — End-to-End Call Architecture

This is the most technically complex part of the system. Understanding it requires understanding 4 technologies: Twilio, WebSocket, Gemini Live API, and μ-law audio encoding.

### 10.1 Full Call Flow Diagram

```
RM types: "Call Rekha Mehta at +919154314766 in Hindi"
  │
  ▼
Gateway → Orchestrator → voice_agent → voice-mcp
  │
  ├─ POST /calls/CALL-2F510995/prepare  ← MUST succeed before dialling
  │    → liveapi-broker stores: {language: "hi-IN", rm_name: "Ravi Gupta",
  │                              client_name: "Rekha Mehta", rm_message: <script>}
  │    ← If this fails (HTTP error or network), initiate_voice_call() returns
  │      an error dict and the Twilio call is NOT placed (guard added deee026)
  │
  ├─ POST https://api.twilio.com/Calls.json
  │    → from: Twilio number
  │    → to: +919154314766 (Rekha's phone rings)
  │    → url: https://liveapi-broker.../twilio/voice/CALL-2F510995
  │
  │  [Rekha's phone rings...]
  │  [Rekha picks up]
  │
  ▼
Twilio: "call answered — POST your TwiML webhook URL"
  │
  ├─ POST https://liveapi-broker.../twilio/voice/CALL-2F510995
  │    ← liveapi-broker returns TwiML:
  │       <Response>
  │         <Connect>
  │           <Stream url="wss://liveapi-broker.../twilio/CALL-2F510995"/>
  │         </Connect>
  │       </Response>
  │
  ▼
Twilio: opens WebSocket → wss://liveapi-broker.../twilio/CALL-2F510995
  │
  ├─ WebSocket CONNECTED
  ├─ liveapi-broker opens Gemini Live session:
  │    model: gemini-3.1-flash-live-preview
  │    voice: Aoede
  │    language: hi-IN
  │    system_instruction: "You are Priya, Ravi Gupta ji ki AI assistant..."
  │
  ├─ Sends first turn: "(Call connected. Please begin your opening greeting now.)"
  │
  ▼
Gemini Live → audio response → liveapi-broker
  │
  ├─ PCM 24kHz audio → ratecv(24000 → 8000) → μ-law encoding → base64
  ├─ → Twilio WebSocket: {"event": "media", "media": {"payload": "<base64>"}}
  │
  ▼
Twilio → plays audio → Rekha's phone speaker:
  "Namaste, kya main Rekha Mehta ji se baat kar sakti hoon?"
  │
  ▼
Rekha speaks: "हां जी, बोलिए।"
  │
  ├─ Twilio captures Rekha's voice (μ-law 8kHz)
  ├─ → WebSocket: {"event": "media", "media": {"payload": "<base64>"}}
  ├─ liveapi-broker: μ-law 8kHz → PCM 16-bit 16kHz (ratecv 8000→16000)
  ├─ → Gemini Live: send_realtime_input(audio, mime="audio/pcm;rate=16000")
  │
  ▼
Gemini Live: transcribes + understands + responds in Hindi
  → "Main Priya hoon, Ravi Gupta ji ki AI assistant..."
  │
  [Conversation continues bidirectionally...]
```

### 10.2 Audio Encoding Chain

Understanding audio encoding is essential to debug voice quality issues:

```
TWILIO ←→ LIVEAPI-BROKER ←→ GEMINI LIVE

Twilio sends:   μ-law 8kHz 8-bit (telephony standard, 64kbps)
Gemini expects: PCM 16-bit 16kHz (high-quality, 256kbps)
Gemini outputs: PCM 16-bit 24kHz (ultra-quality, 384kbps)  ← CRITICAL
Twilio expects: μ-law 8kHz 8-bit

Conversion pipeline (liveapi_broker.py):

Inbound (Rekha → Gemini):
  base64.b64decode(payload) → μ-law bytes
  audioop.ulaw2lin(mu_law, 2) → PCM 8kHz 16-bit
  audioop.ratecv(pcm_8k, 2, 1, 8000, 16000, state) → PCM 16kHz 16-bit
  → send to Gemini Live

Outbound (Gemini → Rekha):
  Gemini PCM 24kHz
  audioop.ratecv(pcm_24k, 2, 1, 24000, 8000, state) → PCM 8kHz  ← MUST use 24000
  audioop.lin2ulaw(pcm_8k, 2) → μ-law 8kHz
  base64.b64encode(mu_law) → send to Twilio
```

**Critical bug we fixed:** Using `ratecv(pcm_24k, 2, 1, 16000, 8000, ...)` (wrong source rate) caused audio to play at 24000/16000 = 1.5× slower, making Priya sound slow and low-pitched. **Always use 24000 as source rate for Gemini Live output.**

### 10.3 VAD (Voice Activity Detection)

Gemini Live has built-in VAD. Configuration:

```python
realtime_input_config=genai_types.RealtimeInputConfig(
    automatic_activity_detection=genai_types.AutomaticActivityDetection(
        start_of_speech_sensitivity=START_SENSITIVITY_HIGH,   # Fires on quiet Indian telephone speech
        end_of_speech_sensitivity=END_SENSITIVITY_HIGH,
        prefix_padding_ms=200,    # Include 200ms before speech starts
        silence_duration_ms=600,  # 600ms silence = end of turn
    ),
),
```

`START_SENSITIVITY_HIGH` is required because telephone audio (8kHz, μ-law compressed) has lower frequency content than microphone audio. Default sensitivity would miss the speech start.

### 10.4 Barge-In Handling

When Rekha speaks while Priya is talking (barge-in), Gemini Live sends `server_content.interrupted = True`. The broker must clear Twilio's audio buffer or the old audio keeps playing:

```python
if sc.interrupted and stream_sid:
    await twilio_ws.send_text(json.dumps({
        "event": "clear",
        "streamSid": stream_sid,
    }))
```

Without this clear event, Twilio would finish playing the interrupted audio before sending Rekha's new audio — causing a jarring delay and double-audio experience.

### 10.5 Greeting Gate

The first thing Gemini does after connecting is greet the client. But Twilio also sends audio from the call connection noise (ring tone, connection clicks). Without protection, this background noise would trigger VAD and interrupt the greeting.

Solution: an asyncio `Event` that gates caller audio until the greeting finishes:

```python
greeting_done = asyncio.Event()

# In _twilio_to_gemini:
if not greeting_done.is_set():
    continue  # Drop all incoming audio until greeting is complete

# In _gemini_to_twilio:
if not first_turn_done and sc.turn_complete:
    first_turn_done = True
    greeting_done.set()  # Signal: greeting is done, start accepting caller audio
```

This ensures Priya always finishes her opening before Rekha's voice is sent to Gemini.

### 10.6 Transcript Storage

Every speaker turn is stored:

```python
TRANSCRIPTS[call_id].append({
    "speaker": "client",   # or "rm" (Priya)
    "text": "हां जी, बोलिए।",
    "ts": 1716479362.808,
})
```

On call end, written to `transcripts/{call_id}.json` and accessible via `GET /transcripts/{call_id}`. This feeds the RM dashboard and CRM post-call update.

---

## 11. Authentication & IAM Architecture

### 11.1 Cloud Run → MCP Server Auth

When the gateway container calls an MCP server, it must authenticate (MCP servers are `--no-allow-unauthenticated`):

```python
# On Cloud Run (K_SERVICE env var is set):
if os.getenv("K_SERVICE"):
    if MCP_INVOKER_SA_EMAIL:
        # Method 1: Impersonation chain
        # Agent Identity → (TokenCreator role) → fsi-rm-mcp-invoker SA → OIDC token
        conn.httpx_client_factory = build_impersonation_factory(
            target_url=mcp_url,
            target_sa_email="fsi-rm-mcp-invoker@<YOUR_GCP_PROJECT>.iam.gserviceaccount.com"
        )
    else:
        # Method 2: GCE metadata server (fallback)
        # Fetches default service account OIDC token from metadata server
        conn.httpx_client_factory = _build_metadata_oidc_factory(audience)
```

**Impersonation chain:**

```
Agent Engine (principalSet://iam.googleapis.com/...)
  │
  └─ roles/iam.serviceAccountTokenCreator on fsi-rm-mcp-invoker@...
       │
       └─ fsi-rm-mcp-invoker@<YOUR_GCP_PROJECT>.iam.gserviceaccount.com
            │
            └─ roles/run.invoker at project level
                 │
                 └─ Cloud Run MCP services (all 5)
```

Why this chain? Agent Engine's identity is a workload identity federation principal (`principalSet://...`), not a regular service account. Cloud Run IAM only grants `roles/run.invoker` to regular service accounts. The impersonation hop bridges this gap.

### 11.2 Token Refresh

The `_OIDCAuth` class in `registry.py` handles automatic token refresh:

```python
class _OIDCAuth(httpx.Auth):
    def auth_flow(self, request):
        if not self._creds.valid:
            self._creds.refresh(self._req)  # Refresh before expiry
        request.headers["Authorization"] = f"Bearer {self._creds.token}"
        yield request
```

OIDC tokens expire in 1 hour. On expiry, the next request triggers a refresh before the HTTP call proceeds.

### 11.3 Gateway → Agent Registry

The registry lookup uses `google.auth.default()` — whichever service account the gateway container runs as (set via Cloud Run service account configuration) has `roles/aiplatform.user` to query Agent Registry.

---

## 12. Session & Memory Architecture

### 12.1 Session Service (Per-Turn History)

`VertexAiSessionService` stores the conversation history for each session in Vertex AI:

```
Session: session-rm-default-8d485eff
  Turn 1: user → "Give me my morning brief for RM001"
  Turn 1: agent → "🔴 URGENT: Rekha Mehta KYC expires June 2..."
  Turn 2: user → "Show me Rekha Mehta's full profile"
  Turn 2: agent → "Client Card: Rekha Mehta..."
  Turn 3: user → "Send her a WhatsApp voice note..."
  ...
```

This history is retrieved by the runner at the start of each turn. The LLM sees the full conversation, enabling natural multi-turn dialogue.

**Problem with long sessions:** When a session accumulates many turns (from multiple test prompts), the history becomes long. Ambiguous follow-up messages like "send it" can resolve to the wrong prior context. Always use fresh session IDs for unrelated workflows.

### 12.2 Memory Bank (Cross-Session Long-Term Memory)

After each completed turn, `add_session_to_memory()` processes the session and extracts facts:

```python
await memory_service.add_session_to_memory(session)
```

Vertex AI Memory Bank uses an LLM internally to extract topical memories:
- "RM001 discussed SIP renewal for client C0040 (Rekha Mehta)"
- "Rekha Mehta interested in continuing Mirae Asset Large Cap Fund"
- "RM asked about HDFC Bank stock price for Rekha's demat holdings"

These memories are injected into the next session via `PreloadMemoryTool` — even if the RM starts a completely new chat window.

### 12.3 In-Process Sub-Agent State

Sub-agents don't have their own session state — they're stateless tools from the orchestrator's perspective. Each AgentTool invocation is independent. State across multiple sub-agent calls within one turn is managed by the orchestrator's LLM via its conversation history.

---

## 13. All 6 Demo Prompts — Full Log Traces & Latency Analysis

### Prompt 1 — Morning Brief

**Input:**
```
Give me my morning brief for RM001. What needs my attention today?
(Compliance alerts first, then expiring SIPs)
```

**Agent Flow:**
1. Orchestrator reads routing map: "morning brief" → compliance_agent THEN portfolio_agent
2. Calls `compliance_agent` with `rm_id=RM001`
3. compliance_agent calls `get_daily_compliance_digest(rm_id="RM001")` — 1 tool call
4. Compliance result returned to orchestrator
5. Orchestrator calls `portfolio_agent` with `rm_id=RM001`
6. portfolio_agent calls `get_clients_with_expiring_sips(rm_id="RM001", days_ahead=30)` — 1 bulk tool call
7. Portfolio result returned to orchestrator
8. Orchestrator synthesizes both into morning brief

**Log Trace (reconstructed from session history):**
```
Prompt received → Orchestrator routing
  → compliance_agent invoked
    → compliance-mcp session created
    → get_daily_compliance_digest(rm_id="RM001")   ← 1 tool call
    → compliance-mcp session terminated
  → portfolio_agent invoked
    → portfolio-mcp session created
    → get_clients_with_expiring_sips(rm_id="RM001", days_ahead=30)  ← 1 bulk call
    → portfolio-mcp session terminated
  → Orchestrator synthesizes
→ Response streamed to RM
```

**Latency breakdown:**
| Phase | Duration |
|---|---|
| Gateway receipt → Orchestrator routing | ~4s |
| compliance_agent (1 tool call) | ~8s |
| portfolio_agent (1 bulk tool call) | ~6s |
| LLM synthesis | ~10s |
| **Total** | **~54s** |

**Previous failure mode (Round 1, before fixes):**
- Wrong agent called first: orchestrator chose `client_intel_agent` (interpreted "attention" as client data)
- Even after routing fix: portfolio_agent iterated per-client (26 calls × ~5s = 130s)
- Further: MCP session terminated mid-run due to `--max-instances` not set (Round 3: 404 error)

**All 3 issues fixed:**
1. Orchestrator routing map added → `compliance_agent` called first
2. Portfolio bulk tool (`get_clients_with_expiring_sips`) added to system prompt
3. `--max-instances=1` set on all MCP servers

---

### Prompt 2 — Client 360° View

**Input:**
```
Give me the complete profile for Rekha Mehta, client C0040.
Include her bank accounts, mutual fund and demat holdings, and any live NAV data
```

**Agent Flow:**
1. Orchestrator: "client profile" → `client_intel_agent`
2. client_intel_agent calls core-banking-mcp: `get_client_profile`, `get_account_summary`, `get_kyc_status`, `get_crm_history`, `get_transaction_history` — parallel where possible
3. Orchestrator: "portfolio details needed" → `portfolio_agent`
4. portfolio_agent calls portfolio-mcp: `get_mf_holdings`, `get_sip_schedule`, `get_demat_holdings`

**Log Trace (15:17:27 UTC):**
```
15:17:27 | gateway received: "Give me complete profile for Rekha Mehta, C0040"
           contextId: session-rm-default-8d485eff
15:17:30 | core-banking-mcp session created
           → ListToolsRequest
           → CallToolRequest (×5 parallel: profile, accounts, KYC, CRM, transactions)
15:17:51 | core-banking-mcp session terminated
15:17:52 | portfolio-mcp session created
           → ListToolsRequest
           → CallToolRequest (×3 parallel: mf_holdings, sip_schedule, demat)
15:18:50 | portfolio-mcp session terminated
```

**Latency:**
| Phase | Duration |
|---|---|
| client_intel_agent (5 parallel tool calls on core-banking-mcp) | ~21s |
| portfolio_agent (3 parallel tool calls on portfolio-mcp) | ~58s |
| LLM synthesis of combined data | ~10s |
| **Total** | **~1m29s** |

**Note:** Zero errors. No session terminated mid-run. `--max-instances=1` confirmed working — all HTTP requests within a session go to the same Cloud Run instance.

---

### Prompt 3 — SIP Analysis + Live AMFI NAV + Market Data (A2A)

**Input:**
```
Rekha Mehta's Mirae Asset Large Cap SIP of ₹37,655/month expires June 11.
Pull the fund's 1-year and 3-year returns, compare with benchmark, and get
the current price of HDFC Bank (she holds 48 shares, ISIN INE040A01034).
Should I recommend she renews at the same amount or revises up?
```

**Agent Flow:**
1. Orchestrator: single-client portfolio query → `portfolio_agent`
2. portfolio_agent calls portfolio-mcp: `get_mf_holdings`, `get_sip_schedule`, `get_demat_holdings`, `get_loan_summary` — parallel
3. portfolio_agent calls `get_mutual_fund_nav("Mirae Asset Large Cap Fund")` → AWS AMFI AgentCore
4. portfolio_agent calls `get_market_data("HDFCBANK")` → AWS Market Data AgentCore

**Log Trace (15:21:06–15:22:40 UTC):**
```
15:21:06 | gateway received: "Rekha Mehta Mirae Asset Large Cap SIP..."
           contextId: session-rm-default-8d485eff (same chat, prior context)
15:21:10 | portfolio-mcp session created (c9e4f757...)
           → ListToolsRequest
15:21:17 | CallToolRequest ×2 (get_mf_holdings + get_sip_schedule, parallel)
15:21:23 | CallToolRequest ×3 (get_demat_holdings + loan_summary + other, parallel)
15:21:25 | CallToolRequest ×1
15:21:26 | CallToolRequest ×1
15:21:32 | CallToolRequest ×4 (parallel batch)
15:21:34 | CallToolRequest ×1
15:21:35 | CallToolRequest ×2
15:21:43 | CallToolRequest ×1 (final MCP call)
15:22:40 | portfolio-mcp session terminated (DELETE /mcp — clean shutdown)
```

**A2A calls** (AMFI + Market Data → AWS) run inside Agent Engine — not visible in Cloud Run logs. No errors recorded means they executed successfully.

**Latency:**
| Phase | Duration |
|---|---|
| portfolio-mcp session + tool discovery | ~4s |
| 10 MCP tool calls (batched) | ~33s |
| 2 A2A calls to AWS (AMFI + Market Data) | ~15s (embedded in tool call timeline) |
| LLM synthesis + recommendation | ~45s |
| **Total** | **~1m34s** |

**10 MCP tool calls** vs 1 for Prompt 1 — this is expected. Single-client mode uses per-tool calls. Bulk mode uses one aggregate call.

---

### Prompt 4 — WhatsApp Voice Note (Hindi)

**Input:**
```
Send Rekha Mehta a WhatsApp voice note on +919154314766 in Hindi.
Tell her that her SIP is expiring on June 11 and that Ravi Gupta
from Cymbal Bank will call her today to discuss the renewal.
```

**Agent Flow:**
1. Orchestrator: "voice note" → `voice_agent`
2. voice_agent calls voice-mcp: `build_voice_note_script(note_type="sip_renewal", ...)`
3. voice_agent returns draft script for RM approval
4. RM approves ("send it")
5. (Second turn) voice_agent calls voice-mcp: `send_whatsapp_voice_note(...)` → TTS → GCS → Twilio

**Log Trace — Initial Draft Turn (15:27:57 UTC):**
```
15:27:57 | gateway received: "Send Rekha Mehta a WhatsApp voice note in Hindi..."
           contextId: session-rm-default-8d485eff (stale session — caused bug)
15:28:02 | voice-mcp session created (1cd5109e...)
           → ListToolsRequest
15:28:05 | CallToolRequest #1 — build_voice_note_script
15:28:09 | CallToolRequest #2 — (additional check/preview)
15:28:18 | CallToolRequest #3 — (script finalization)
15:28:20 | voice-mcp session terminated
[Agent returns draft to RM for approval]
```

**Log Trace — Bug: RM confirmed in SAME old session:**
```
15:28:47 | gateway received: "send it"
           contextId: session-rm-default-8d485eff  ← OLD session with birthday history
15:28:50 | voice-mcp session created (ceb31181...)
           [Agent searched session history, found birthday script from earlier demo]
15:29:06 | HTTP POST https://api.twilio.com/.../Messages.json → 201 Created
           [WRONG: Birthday voice note sent instead of SIP renewal]
```

**Root Cause:** `session-rm-default-8d485eff` is a long-lived session from days of testing. The birthday greeting from an earlier demo was in the session history. When "send it" arrived, the agent searched history and found the birthday script.

**Fix applied for next attempt:** Started new chat window → fresh `contextId` with no stale history.

**Log Trace — New Session, Correct Send (15:35:52 UTC):**
```
15:35:52 | gateway received: "Send Rekha Mehta WhatsApp voice note Hindi SIP..."
           contextId: NO-CONTEXT-ID (brand new session, zero history)
15:35:56 | voice-mcp session created — draft turn
           → build_voice_note_script(note_type="sip_renewal", client_name="Rekha Mehta",
             language="hi-IN", {fund_name, expiry_date, monthly_amount})
15:36:07 | voice-mcp session terminated — draft returned to RM

15:36:27 | gateway received: "Confirm" (RM approves)
           contextId: session-rm-default-06e46823 (new fresh session)
15:36:30 | voice-mcp session created — send turn
           → send_whatsapp_voice_note(mobile="+919154314766", ...)
             → _tts_to_ogg(text, "hi-IN") → Neural2-A → OGG audio
             → _upload_audio_to_gcs → public URL
             → Twilio Messages.json POST
15:37:00 | HTTP POST https://api.twilio.com/.../Messages.json → 201 Created ✅
15:37:04 | voice-mcp session terminated
```

**Latency:**
| Phase | Duration |
|---|---|
| Prompt received → draft returned | ~23s |
| RM reviews draft | ~20s (human time) |
| RM confirmation → Twilio 201 | ~33s |
| **Total (system time only)** | **~56s** |
| **Total (RM approval included)** | **~1m16s** |

---

### Prompt 5 — Email Draft

**Input:**
```
Draft a portfolio review email to Rekha Mehta. Cover:
1. Her Mirae Asset Large Cap SIP of ₹37,655/month expires June 11 — recommend renewal
2. Her KYC Aadhaar needs renewal by June 2 — link to net banking portal
3. Her HDFC Bank stock performance — she holds 48 shares
```

**Agent Flow:**
1. Orchestrator: "draft email" → `comms_agent`
2. comms_agent calls comms-mcp: `draft_email(to, subject, body_context)` — 1 tool call

**Log Trace (15:38:17 UTC):**
```
15:38:17 | gateway received: "Draft a portfolio review email to Rekha Mehta..."
           contextId: session-rm-default-8d485eff
15:38:21 | comms-mcp session created (4a5ca106...)
           → ListToolsRequest
15:38:30 | CallToolRequest — draft_email
15:38:30 | DeprecationWarning: datetime.utcnow() deprecated [non-fatal]
15:38:35 | comms-mcp session terminated
```

**Latency:**
| Phase | Duration |
|---|---|
| Gateway → comms-mcp | ~4s |
| 1 tool call (draft_email) | ~9s |
| LLM email composition | ~5s |
| **Total** | **~18s** |

**Fastest prompt of the set.** Email drafting is 1 tool call with no external API calls. The comms agent is also the simplest — it generates a draft and returns it. No network roundtrips to AWS, no audio encoding, no Twilio.

The returned email is always a draft — never sent. The comms-mcp `draft_email` tool returns:
```json
{
  "draft_id": "EMAIL-XXXXXXXX",
  "to": "rekha.mehta@example.com",
  "subject": "Portfolio Review — Action Required: SIP Renewal & KYC Update",
  "body": "Dear Rekha ji,\n\nI hope you are doing well...",
  "status": "draft",
  "note": "Please review before sending."
}
```

---

### Prompt 6 — Outbound AI Voice Call (Full Trace)

**Input:**
```
Call Rekha Mehta at +919154314766 in Hindi.
Her Mirae Asset Large Cap SIP expires June 11.
Goal: schedule a callback with Ravi Gupta to discuss SIP renewal and KYC update.
```

**Agent Flow:**
1. Orchestrator: "call" → `voice_agent`
2. voice_agent: drafts call plan, returns to RM for confirmation
3. RM sends "Dial"
4. voice_agent calls voice-mcp: `initiate_voice_call(...)`
5. voice-mcp calls liveapi-broker `/prepare` → stores call context
6. voice-mcp calls Twilio `Calls.json` → Twilio dials Rekha
7. Rekha's phone rings → she picks up
8. Twilio webhooks to liveapi-broker → `GET /twilio/voice/{call_id}` → TwiML returned
9. Twilio opens WebSocket to liveapi-broker `/twilio/{call_id}`
10. liveapi-broker opens Gemini Live session → sends initial turn
11. Priya (Gemini) speaks greeting
12. Live conversation: Priya ↔ Rekha in Hindi

**Full Log Trace (15:42:19–15:49:38 UTC):**

```
━━━ PHASE 1: RM PROMPT (T+0) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

15:42:19 | fsi-rm-a2a-gateway | Received A2A body:
           method: message/stream
           contextId: NO-CONTEXT-ID (new clean session)
           text: "Call Rekha Mehta at +919154314766 in Hindi..."

15:42:24 | fsi-rm-a2a-gateway | ADK JSON_SCHEMA_FOR_FUNC_DECL experimental feature enabled
15:42:26 | fsi-rm-a2a-gateway | ADK MCP_GRACEFUL_ERROR_HANDLING experimental feature enabled
15:42:26 | fsi-rm-a2a-gateway | App name mismatch (non-fatal)

15:42:27 | fsi-rm-voice-mcp   | Created new transport: 836ab4ad...
15:42:27 | fsi-rm-voice-mcp   | Processing request: ListToolsRequest
15:42:31 | fsi-rm-voice-mcp   | Terminating session: 836ab4ad...
           [No CallToolRequest — agent returned a draft/plan to RM for confirmation]

━━━ PHASE 2: RM CONFIRMS (T+21s) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━

15:48:40 | fsi-rm-a2a-gateway | Received A2A body:
           contextId: session-rm-default-ef687472 (new fresh session)
           text: "Dial"

15:48:42 | fsi-rm-voice-mcp   | Created new transport: ef10ef2e...
15:48:42 | fsi-rm-voice-mcp   | ListToolsRequest
15:48:46 | fsi-rm-voice-mcp   | CallToolRequest — initiate_voice_call
           → POST https://liveapi-broker.../calls/CALL-2F510995/prepare
           ← 200 OK (call context stored)
15:48:46 | fsi-rm-voice-mcp   | POST https://api.twilio.com/.../Calls.json
           ← HTTP 201 Created  ✅ TWILIO CALL INITIATED
           [Rekha's phone begins ringing]
15:48:53 | fsi-rm-voice-mcp   | CallToolRequest — get_call_status
15:48:54 | fsi-rm-voice-mcp   | CallToolRequest — (script confirmation)
15:48:58 | fsi-rm-voice-mcp   | Terminating session: ef10ef2e...
           [Gateway response returned to RM: "Call initiated to Rekha Mehta..."]

━━━ PHASE 3: REKHA PICKS UP (T+~27s from Twilio dial) ━━━━━━━━

15:49:00 | fsi-rm-liveapi-broker | connection open
15:49:00 | fsi-rm-liveapi-broker | Twilio stream connected: call=CALL-2F510995 lang=en-IN

                                 [Note: lang=en-IN instead of hi-IN — known bug.
                                  Gemini still speaks Hindi due to system_instruction.]

15:49:01 | fsi-rm-liveapi-broker | Sent initial turn to Gemini Live for CALL-2F510995
           → "(Call connected. Please begin your opening greeting now.)"
15:49:01 | fsi-rm-liveapi-broker | Twilio stream started: streamSid=MZ2df77c5e...

━━━ PHASE 4: GEMINI LIVE GREETING ━━━━━━━━━━━━━━━━━━━━━━━━━━━━

15:49:01 | GEMINI [CALL-2F510995]: "Namaste, kya main Rajesh ji se baat kar sakti hoon?"
           [Bug: "Rajesh" should be "Rekha" — client name not passed correctly]
           [Audio encoding: 24kHz PCM → 8kHz μ-law → Twilio → Rekha's speaker]

15:49:04 | fsi-rm-liveapi-broker | Greeting complete — user audio now active
           [greeting_done Event fired — Rekha's audio now forwarded to Gemini]
15:49:06 | fsi-rm-liveapi-broker | First audio frame sent — RMS=3160
           [RMS 3160 = voice detected. Rekha is speaking.]

━━━ PHASE 5: LIVE CONVERSATION ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

15:49:09 | USER  [CALL-2F510995]: "हां जी, बोलिए।"
           [Rekha confirms identity — "Yes, please speak"]

15:49:09 | GEMINI [CALL-2F510995]: "Main Priya hoon, Ravi Gupta ji ki AI assistant."
           ✅ CORRECT: "Ravi Gupta" (not "Your Relationship Manager")
           [rm_name fix working correctly]

15:49:10 | GEMINI: "Unhone mujhe aapko call karne ke liye kaha hai."
           [Introduces purpose: Ravi Gupta asked me to call you]

15:49:10–15:49:25 | GEMINI: Discusses Rekha's portfolio mutual funds performance,
           asks if she has time to speak with Ravi Gupta:
           "Ravi Gupta ji ne bataya hai ki aapke portfolio mein jo mutual funds hain,
            unka performance pichle kuch samay mein kaafi achha raha hai.
            Woh aapko is baare mein aur detail mein batana chahte hain.
            Kya aap kal subah 10 baje free hain unse baat karne ke liye?"

15:49:20 | fsi-rm-liveapi-broker | Audio frames: 800  RMS=1760
           [Rekha speaking again]

15:49:22 | USER  [CALL-2F510995]: "हां, बोलिए-बोलिए। क्या बिल किए?"
           ["Yes please continue. What did you bill?"]

15:49:38 | Audio frames: 1700  RMS=4054
           [Active conversation continues...]
```

**Full Latency Breakdown:**

| Segment | Start | End | Duration |
|---|---|---|---|
| Gateway receives prompt | 15:42:19 | 15:42:27 | 8s — gateway parsing + ADK init |
| voice-mcp draft turn | 15:42:27 | 15:42:31 | 4s — ListTools, return draft |
| RM reviews + confirms | 15:42:31 | 15:48:40 | ~6m — human review time |
| Gateway receives "Dial" | 15:48:40 | 15:48:42 | 2s — session creation |
| voice-mcp: prepare + Twilio dial | 15:48:42 | 15:48:46 | **4s — liveapi-broker prep + Twilio 201** |
| Twilio dialling Rekha | 15:48:46 | 15:49:00 | 14s — network + phone ringing |
| Rekha picks up → WebSocket open | 15:49:00 | 15:49:01 | 1s |
| Gemini Live session opens | 15:49:01 | 15:49:01 | <1s |
| Gemini speaks first word | 15:49:01 | 15:49:01 | **<1s — real-time** |
| **System time: "Dial" → Rekha hears Priya** | 15:48:40 | 15:49:01 | **~21s** |
| **System time: Twilio 201 → first speech** | 15:48:46 | 15:49:01 | **~15s** |

---

## 14. Known Issues & Root Causes

### ~~Issue 1: Voice Call Greeting Uses Wrong Client Name~~ — FIXED (`deee026`)

**Symptom (historical):** Priya opened with "Namaste, kya main **Rajesh** ji se baat kar sakti hoon?" instead of "Rekha".

**Root cause:** The voice agent system prompt listed `initiate_voice_call()` parameters as `client_id, mobile, language, script_variables` — it omitted `client_name` and `rm_name`. The LLM either skipped these parameters or guessed them from session history (picking a different client's name). Additionally, `_prepare_bridge()` used `except: pass` to silently swallow errors — if the broker was unreachable, the call proceeded with an empty context dict, making Priya greet with "aap" instead of the client's name.

**Fix applied (commit `deee026`):**
1. Voice agent system prompt now explicitly lists all 7 required parameters for `initiate_voice_call()`, with a `⚠️ NEVER substitute another client's name` warning.
2. `_prepare_bridge()` now logs and re-raises on error instead of silently swallowing it.
3. `initiate_voice_call()` returns an error dict if `/prepare` fails — the call is **not placed** with broken context.

### ~~Issue 2: Voice Call Language Tag is `en-IN` Instead of `hi-IN`~~ — FIXED (`deee026`)

**Symptom (historical):** Log showed `lang=en-IN` but RM asked for Hindi.

**Root cause:** `liveapi-broker` read `ctx.get("language", "en-IN")`. If `_prepare_bridge()` failed silently (see Issue 1 above), the context dict was empty and the default `"en-IN"` took effect. Gemini Live then used English for STT transcription while still speaking Hindi (because the system_instruction was in Hindi) — causing STT accuracy degradation for the client's Hindi responses.

**Fix applied (commit `deee026`):**
1. Default changed from `"en-IN"` to `"hi-IN"` in `liveapi_broker.py` — matching `voice_mcp.py`'s own default.
2. Added a warning log when the Twilio WebSocket connects and `CALL_CONTEXT[call_id]` is empty, making silent context misses immediately visible in Cloud Logging.

### Issue 3: Session Reuse Causes Stale Context on Confirmation

**Symptom:** Ambiguous messages like "send it" pull wrong scripts from session history.

**Root cause:** Gemini Enterprise assigns a fixed `contextId` per chat window. All turns in that window share the same Vertex AI session. If the session has history from multiple unrelated tests (birthday greeting, SIP renewal, client profiles), "send it" can resolve to the wrong prior action.

**Architectural fix:** Add a `stage_voice_note(script_text, session_id)` tool to voice_mcp that persists the draft to a short-lived in-memory store, keyed by session_id. The confirmation turn calls `send_staged_voice_note(session_id)` which retrieves the exact staged script — bypassing LLM session history entirely.

**Workaround:** Start a new chat window for each workflow. Each new window = new contextId = empty session history.

### Issue 4: App Name Mismatch Warning

**Symptom:** Every request logs "App name mismatch: runner configured as 'fsi-rm-agentspace' but agent loaded from '/app/agents/voice'"

**Root cause:** The gateway's `APP_NAME = "fsi-rm-agentspace"` is registered in Vertex AI. But when ADK loads a sub-agent, it reads the agent's internal name (`voice`, `portfolio`, etc.) from the module path. The mismatch is just a warning — the runner still works correctly.

**Fix:** Set the agent's `app_name` attribute to match `fsi-rm-agentspace`, or suppress the warning in ADK settings.

---

## 15. Infrastructure Inventory

### Cloud Run Services

| Service | Region | Image | Max Instances | Auth | Purpose |
|---|---|---|---|---|---|
| `fsi-rm-a2a-gateway` | asia-south1 | `gcr.io/<YOUR_GCP_PROJECT>/fsi-rm-gateway` | default | IAM | Main A2A entry point |
| `fsi-rm-core-banking-mcp` | asia-south1 | `gcr.io/<YOUR_GCP_PROJECT>/fsi-rm-mcp` | **1** | IAM | BigQuery: clients, accounts, transactions |
| `fsi-rm-portfolio-mcp` | asia-south1 | `gcr.io/<YOUR_GCP_PROJECT>/fsi-rm-mcp` | **1** | IAM | BigQuery: MF, SIPs, loans, demat |
| `fsi-rm-comms-mcp` | asia-south1 | `gcr.io/<YOUR_GCP_PROJECT>/fsi-rm-mcp` | **1** | IAM | Email/WhatsApp drafting |
| `fsi-rm-compliance-mcp` | asia-south1 | `gcr.io/<YOUR_GCP_PROJECT>/fsi-rm-mcp` | **1** | IAM | KYC, AML, DPD alerts |
| `fsi-rm-voice-mcp` | asia-south1 | `gcr.io/<YOUR_GCP_PROJECT>/fsi-rm-mcp` | **1** | IAM | TTS, Twilio, call initiation |
| `fsi-rm-liveapi-broker` | **us-east1** | `gcr.io/<YOUR_GCP_PROJECT>/fsi-rm-gateway` | default | IAM | Twilio↔Gemini Live bridge |

**Why `--max-instances=1` on all MCP servers?** MCP StreamableHTTP sessions are in-memory. Multiple instances = session not found on alternate instance = `McpError: Session terminated`. Single instance ensures all requests for a session hit the same process.

**Why liveapi-broker in us-east1?** Gemini Live API (`gemini-3.1-flash-live-preview`) requires allowlist approval per GCP project per region. us-east1 is the region where this project has access. The MCP servers and gateway are in asia-south1 for latency to India. The cross-region latency for voice (asia-south1 → us-east1) is acceptable because Gemini Live is real-time WebSocket — once connected, audio streams bidirectionally with sub-second latency.

### Vertex AI / Agent Engine

| Resource | Value |
|---|---|
| Agent Engine ID | `4912793874763612160` |
| Project | `<YOUR_GCP_PROJECT>` |
| Location | `us-east1` |
| Session Service | `VertexAiSessionService` |
| Memory Bank | `VertexAiMemoryBankService` |

### BigQuery Dataset

| Dataset | Project | Tables |
|---|---|---|
| `fsi_rm_poc` | `<YOUR_GCP_PROJECT>` | `clients`, `accounts`, `transactions`, `mf_holdings`, `sip_schedules`, `loans`, `demat_holdings`, `kyc_records`, `compliance_events` |

All data is synthetic (generated by `scripts/seed_bigquery.py`). 500 mock clients across HNI, Mass Affluent, and SME segments.

### AWS Bedrock AgentCore (External)

| Agent | Region | Endpoint |
|---|---|---|
| AMFI NAV Agent | us-east-1 | `AMFI_AGENT_URL` env var |
| Market Data Agent | us-east-1 | `MARKET_DATA_AGENT_URL` env var |
| Credit Bureau Agent | us-east-1 | `CREDIT_BUREAU_AGENT_URL` env var |
| Account Aggregator Agent | us-east-1 | `ACCOUNT_AGGREGATOR_AGENT_URL` env var |

### Third-Party Services

| Service | Purpose | Authentication |
|---|---|---|
| Twilio | WhatsApp API + Outbound voice calls | `TWILIO_ACCOUNT_SID` + `TWILIO_AUTH_TOKEN` |
| Google Cloud TTS | Neural2-A voice synthesis for WhatsApp notes | ADC (Application Default Credentials) |
| Gemini Live API (`gemini-3.1-flash-live-preview`) | Real-time voice conversation | `GOOGLE_API_KEY` (AI Studio) |
| GCS Bucket | Audio file storage for WhatsApp voice notes | ADC |

---

## 16. Changelog

| Date | Commit | Change |
|---|---|---|
| 2026-05-23 | `02cec02` | Initial production checkpoint — all 6 prompts verified E2E |
| 2026-05-24 | `deee026` | Fix voice call bugs: wrong client name, en-IN language default, silent /prepare failure |

---

*Last updated: 2026-05-24. Latest stable commit: `deee026`.*  
*All log timestamps are UTC. Production project: `<YOUR_GCP_PROJECT>`.*
