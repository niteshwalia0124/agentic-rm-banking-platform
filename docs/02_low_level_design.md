# Low Level Design — Agent Teams for Relationship Managers

**Document Version:** 1.0  
**Date:** 2026-05-29  
**Status:** Production  
**Project:** <YOUR_GCP_PROJECT> (GCP) | agentic-rm-banking (GitHub)

---

## Table of Contents

1. [Repository Structure](#1-repository-structure)
2. [Agent Layer — Implementation Details](#2-agent-layer)
3. [MCP Server Layer](#3-mcp-server-layer)
4. [LiveAPI Broker](#4-liveapi-broker)
5. [A2A Gateway](#5-a2a-gateway)
6. [External A2A Agents (AWS)](#6-external-a2a-agents)
7. [API Specifications](#7-api-specifications)
8. [Data Models](#8-data-models)
9. [Voice Pipeline — End to End](#9-voice-pipeline)
10. [Build & Deploy Pipeline](#10-build--deploy-pipeline)
11. [Environment Variables Reference](#11-environment-variables-reference)
12. [Security Implementation](#12-security-implementation)
13. [Error Handling Patterns](#13-error-handling-patterns)

---

## 1. Repository Structure

```
agentic-rm-banking/
├── agents/
│   ├── common/
│   │   └── registry.py          # MCP discovery (Agent Registry → localhost fallback)
│   ├── orchestrator/
│   │   ├── agent.py             # Root LlmAgent, 5 sub-agents, PreloadMemoryTool
│   │   └── deploy_agent.py      # Vertex AI Agent Engine deployment script
│   ├── compliance/agent.py      # Compliance & risk LlmAgent
│   ├── client_intel/agent.py    # 360° client view LlmAgent
│   ├── portfolio/agent.py       # Portfolio analytics LlmAgent
│   ├── comms/agent.py           # Communications LlmAgent
│   └── voice/agent.py           # Voice outreach LlmAgent
├── mcp_servers/
│   ├── Dockerfile               # Shared image for all 5 MCP servers
│   ├── voice_mcp.py             # Voice tools: TTS, WhatsApp, outbound calls
│   ├── compliance_mcp.py        # KYC/AML/regulatory tools
│   ├── portfolio_mcp.py         # MF holdings, SIPs, loan tools
│   ├── core_banking_mcp.py      # Account/transaction tools
│   └── comms_mcp.py             # Gmail, WhatsApp text tools
├── bridge/
│   └── liveapi_broker.py        # Twilio ↔ Gemini Live WebSocket bridge
├── gateway/
│   └── a2a_server.py            # A2A JSON-RPC endpoint
├── external_agents/
│   ├── a2a_client.py            # SigV4-signed AWS AgentCore client
│   ├── amfi_agent/              # AMFI NAV data agent (deployed on AWS)
│   ├── market_data_agent/       # BSE/NSE price agent (deployed on AWS)
│   ├── credit_bureau_agent/     # CIBIL/Experian agent (deployed on AWS)
│   └── account_aggregator_agent/# RBI AA Framework agent (deployed on AWS)
├── infra/
│   └── main.tf                  # IAM, Agent Registry, Agent Gateway
├── scripts/
│   └── seed_bigquery.py         # Synthetic client data seeder
├── docs/                        # This document and architecture diagrams
├── tests/                       # Unit + integration tests
├── cloudbuild.yaml              # MCP + gateway image build
├── cloudbuild-bridge.yaml       # LiveAPI broker image build
└── requirements.txt             # Python dependencies
```

---

## 2. Agent Layer

### 2.1 Orchestrator Agent (`agents/orchestrator/agent.py`)

**Type:** `LlmAgent`  
**Model:** `gemini-3.5-flash` (via `GEMINI_MODEL` env var)  
**Deployed:** Vertex AI Agent Engine (Reasoning Engine ID: `8386758037326528512`)

**Tools registered:**
| Tool | Type | Purpose |
|---|---|---|
| `PreloadMemoryTool` | Built-in ADK | Loads Memory Bank context at start of every session |
| `list_mcp_connections` | FunctionTool | Shows RM which MCP servers are live |
| `compliance_agent` | AgentTool | KYC/AML/regulatory queries |
| `client_intel_agent` | AgentTool | 360° client profile |
| `portfolio_agent` | AgentTool | Portfolio analytics |
| `comms_agent` | AgentTool | Email/WhatsApp drafting |
| `voice_agent` | AgentTool | Voice notes and outbound calls |

**Routing logic (from system prompt):**
```
"morning brief" / "attention" → compliance_agent + portfolio_agent (parallel intent)
"client profile" / "tell me about" → client_intel_agent + portfolio_agent
"email" / "draft" / "WhatsApp text" → comms_agent
"voice note" / "WhatsApp audio" / "call" / "phone" → voice_agent
"KYC" / "compliance" / "AML" → compliance_agent
```

**Pickle safety:** `_PickleSafeOrchestrator.__reduce__` returns `(_build_orchestrator, ())`. Agent Engine pickles agent between invocations; `__reduce__` ensures fresh MCP discovery on unpickle instead of deserializing stale connections.

**Deployment:**
```bash
python agents/orchestrator/deploy_agent.py \
  --project=<YOUR_GCP_PROJECT> \
  --location=us-east1 \
  --model=gemini-3.5-flash
```

---

### 2.2 Specialist Sub-Agents

All sub-agents follow the same pattern:

```python
def _build_agent() -> LlmAgent:
    return _PickleSafeAgent(
        name="<agent_name>",
        model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash"),
        description="<routing description for orchestrator>",
        instruction=SYSTEM_PROMPT,
        tools=[
            discover_mcp_toolset(
                display_name="FSI-RM <Domain> MCP",
                fallback_env_var="<DOMAIN>_MCP_URL",
                fallback_default="http://localhost:<port>",
            )
        ],
        on_tool_error_callback=handle_tool_error,
    )
```

| Agent | MCP Server | Port | Key Tools |
|---|---|---|---|
| `compliance_agent` | `fsi-rm-compliance-mcp` | 8004 | `get_kyc_status`, `get_aml_alerts`, `get_daily_compliance_digest` |
| `client_intel_agent` | `fsi-rm-core-banking-mcp` | 8001 | `get_client_profile`, `get_account_summary`, `get_transaction_history` |
| `portfolio_agent` | `fsi-rm-portfolio-mcp` | 8002 | `get_mf_holdings`, `get_sip_mandates`, `get_expiring_sips`, `get_loan_details` |
| `comms_agent` | `fsi-rm-comms-mcp` | 8003 | `draft_email`, `send_email`, `send_whatsapp_text` |
| `voice_agent` | `fsi-rm-voice-mcp` | 8005 | `build_voice_note_script`, `send_whatsapp_voice_note`, `initiate_voice_call` |

**portfolio_agent additional tools (AWS cross-cloud via FunctionTool):**
```python
def get_mutual_fund_nav(fund_name: str) -> dict:
    return asyncio.run(a2a_call(os.getenv("AMFI_AGENT_URL"), fund_name))

def get_market_data(symbol: str) -> dict:
    return asyncio.run(a2a_call(os.getenv("MARKET_DATA_AGENT_URL"), symbol))
```

**client_intel_agent additional tools (AWS cross-cloud):**
```python
def get_credit_bureau_report(pan: str) -> dict:
    return asyncio.run(a2a_call(os.getenv("CREDIT_BUREAU_AGENT_URL"), pan))

def get_account_aggregator_data(customer_id: str) -> dict:
    return asyncio.run(a2a_call(os.getenv("ACCOUNT_AGGREGATOR_AGENT_URL"), customer_id))
```

---

## 3. MCP Server Layer

### 3.1 Shared Infrastructure

All 5 MCP servers are built from the same Docker image (`gcr.io/<YOUR_GCP_PROJECT>/fsi-rm-mcp`) and selected at runtime via the `MCP_SERVER` environment variable:

```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mcp_servers/ ./mcp_servers/
CMD python mcp_servers/${MCP_SERVER}.py
```

All servers use `FastMCP` with DNS rebinding protection disabled (required for Cloud Run):
```python
_no_dns_rebinding = TransportSecuritySettings(enable_dns_rebinding_protection=False)
mcp = FastMCP("server-name", transport_security=_no_dns_rebinding)
```

### 3.2 Voice MCP (`voice_mcp.py`) — Deep Dive

This is the most complex MCP server. Key in-memory state:

```python
_calls: dict[str, dict] = {}                    # call records by call_id
_pending_scripts: dict[str, dict] = {}           # voice note scripts by note_id
_latest_note_by_mobile: dict[str, str] = {}      # client_name → latest note_id (first-build-wins)
```

**Tool: `build_voice_note_script()`**

```
Input:  note_type, client_name, language, script_variables
Output: {note_id, message_text, language_name, char_count, instruction}
```

Script locking algorithm:
1. Fill template from `_VOICE_NOTE_TEMPLATES[note_type]` with `script_variables`
2. Generate `NOTE-{uuid8}` as note_id
3. **If** `client_name.lower()` NOT in `_latest_note_by_mobile`:
   - Store in `_pending_scripts[note_id]`
   - Set `_latest_note_by_mobile[client_name.lower()] = note_id`
   - Log: "Script stored (first)"
4. **Else**:
   - Discard — do NOT store in `_pending_scripts`
   - Log: "Script discarded (not stored) — locked build already set"

This prevents Memory Bank contamination from overwriting the first approved script.

**Tool: `send_whatsapp_voice_note()`**

```
Input:  mobile, client_name, note_id (preferred), message_text (fallback), language, rm_id
Output: {note_id, status, twilio_message_sid, audio_url, listen_url, message}
```

Script resolution:
```python
resolved_note_id = note_id
if not resolved_note_id or resolved_note_id not in _pending_scripts:
    resolved_note_id = _latest_note_by_mobile.get(client_name.lower(), "")

if resolved_note_id and resolved_note_id in _pending_scripts:
    stored = _pending_scripts.pop(resolved_note_id)
    message_text = stored["message_text"]  # exact approved text
```

TTS pipeline:
```python
def _tts_to_ogg(text: str, language_code: str) -> bytes:
    client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
    prompt = _build_tts_prompt(text, language_code)  # Director's Notes prefix
    response = client.models.generate_content(
        model="gemini-3.1-flash-tts-preview",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Autonoe")
                )
            ),
        ),
    )
    pcm_bytes = response.candidates[0].content.parts[0].inline_data.data
    audio = AudioSegment.from_raw(io.BytesIO(pcm_bytes), sample_width=2, frame_rate=24000, channels=1)
    ogg_buf = io.BytesIO()
    audio.export(ogg_buf, format="ogg", codec="libopus")
    return ogg_buf.getvalue()
```

**Tool: `initiate_voice_call()`**

```
Input:  client_id, mobile, client_name, call_type, script_variables, rm_id, rm_name, language
Output: {call_id, status="initiated", twilio_call_sid, IMPORTANT="CALL IS NOW LIVE. Do NOT report..."}
```

Call flow:
1. Fill agenda from `AGENDAS[call_type]` with `script_variables`
2. Extract first names: `client_first = client_name.split()[0]`
3. Build Priya's full system instruction via `_build_system_instruction()`
4. POST to `LIVEAPI_BROKER_URL/calls/{call_id}/prepare` with system instruction
5. POST to Twilio Calls API — Twilio dials mobile → connects to broker WebSocket
6. Return `call_id` with `IMPORTANT` field explicitly warning agent not to fabricate outcome

**Priya system instruction structure:**
```
⚠️ GENDER RULE (first line — Gemini Live attends to start of context)
You are Priya, female AI voice assistant for {rm_name}...
━━━ HOW TO ADDRESS THE CLIENT ━━━
  - First name only: "Amit ji", "Rekha ji" (never full name except open/close)
  - Male: Sir / ji | Female: Ma'am / ji
━━━ CALL STRUCTURE ━━━
  STEP 1 — OPENING: "Namastey {client_first} ji! Main Priya hoon..."
  STEP 2 — STATE THE AGENDA: {agenda}
  STEP 3 — LISTEN AND NOTE
  STEP 4 — HANDLE RESPONSE (YES/NO/UNSURE/SCHEDULE)
  STEP 5 — CLOSING: "Aapka bahut bahut shukriya {client_first} ji."
━━━ WHAT YOU NEVER DO ━━━
```

### 3.3 SIP Renewal Template

```python
"sip_renewal": (
    "{greeting} {client_name} ji! Cymbal Bank ki taraf se. "
    "Aapka {fund_name} SIP {expiry_date} expire ho raha hai. "
    "Renewal ke liye please humse sampark karein. {closing}!"
)
```

Note: `monthly_amount` intentionally removed — prevented agent from hallucinating amounts from Memory Bank.

---

## 4. LiveAPI Broker

**File:** `bridge/liveapi_broker.py`  
**Deployed:** `fsi-rm-liveapi-broker` (Cloud Run, us-east1, **max-instances=1**)

Max-instances=1 is mandatory: `CALL_CONTEXT` dict is in-memory. If two instances exist, `/calls/{call_id}/prepare` and the WebSocket `/twilio/{call_id}` may hit different instances, causing the Gemini Live session to start with no context.

### 4.1 Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/health` | GET | Returns `{status: "ok", active_calls: N}` |
| `/calls/{call_id}/prepare` | POST | Stores call context (system instruction, language, client_name, rm_name) before Twilio dials |
| `/twilio/{call_id}` | WebSocket | Receives Twilio Media Stream; bridges to Gemini Live |
| `/twilio/status/{call_id}` | POST | Twilio call status webhook (logs completed/failed) |
| `/calls/{call_id}/transcript` | GET | Returns in-progress or completed transcript |

### 4.2 Audio Pipeline

```
Client phone → Twilio (μ-law 8kHz) → WebSocket → Broker
  → audioop.ulaw2lin() → PCM 16-bit
  → audioop.ratecv(8000→16000) → PCM 16kHz
  → Gemini Live (PCM 16kHz input)

Gemini Live (PCM 24kHz output) → Broker
  → audioop.ratecv(24000→8000) → PCM 8kHz
  → audioop.lin2ulaw() → μ-law 8kHz
  → Twilio → Client phone
```

### 4.3 Transcript Accumulation

```python
# In _gemini_to_twilio():
_last_user_text = ""   # += on each input_transcription chunk
_last_gemini_text = "" # += on each output_transcription chunk

# On turn_complete:
TRANSCRIPTS[call_id].append({"speaker": "client", "text": _last_user_text, "ts": ts})
TRANSCRIPTS[call_id].append({"speaker": "priya",  "text": _last_gemini_text, "ts": ts})
_last_user_text = _last_gemini_text = ""

# In finally block (fires even if client hangs up before turn_complete):
if _last_user_text or _last_gemini_text:
    TRANSCRIPTS[call_id].append(...)  # flush unflushed text
```

### 4.4 Transcript Storage

```python
def _save_transcript(call_id: str) -> None:
    segments = TRANSCRIPTS.pop(call_id, [])
    log.info("Saving transcript for call=%s — %d segments", call_id, len(segments))
    if not segments:
        log.warning("No transcript segments for call=%s", call_id)
        return
    # Local (ephemeral fallback)
    path = os.path.join(TRANSCRIPT_DIR, f"{call_id}.json")
    with open(path, "w") as f:
        f.write(json.dumps(segments, ensure_ascii=False, indent=2))
    # GCS (permanent compliance record)
    bucket = gcs.Client().bucket(TRANSCRIPT_BUCKET)
    blob = bucket.blob(f"{call_id}.json")
    blob.upload_from_string(payload, content_type="application/json")
    log.info("Transcript uploaded: gs://%s/%s.json", TRANSCRIPT_BUCKET, call_id)
```

---

## 5. A2A Gateway

**File:** `gateway/a2a_server.py`  
**Deployed:** `fsi-rm-a2a-gateway` (Cloud Run, us-east1, min-instances=1)

### 5.1 Request Flow

```
POST / (A2A JSON-RPC)
  ↓
Parse contextId from params.message.contextId  ← Gemini Enterprise sends here
  ↓
Create ADK InMemorySessionService session
  ↓
Invoke Vertex AI Agent Engine (Reasoning Engine)
  ↓
Stream response back to Gemini Enterprise via message/stream
```

### 5.2 A2A Message Format

**Inbound (from Gemini Enterprise):**
```json
{
  "jsonrpc": "2.0",
  "method": "message/stream",
  "id": "<uuid>",
  "params": {
    "message": {
      "contextId": "session-rm-default-<hash>",
      "kind": "message",
      "parts": [{"kind": "text", "text": "RM prompt here"}],
      "role": "user"
    }
  }
}
```

**Note:** Gemini Enterprise sends session ID in `params.message.contextId`, NOT `params.sessionId`. This is a critical implementation detail discovered in production.

---

## 6. External A2A Agents

**File:** `external_agents/a2a_client.py`

### 6.1 SigV4 Auth

```python
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials

credentials = Credentials(
    access_key=os.getenv("AWS_ACCESS_KEY_ID"),
    secret_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
)
request = AWSRequest(method="POST", url=agent_url, data=body)
SigV4Auth(credentials, "bedrock-agentcore", "us-east-1").add_auth(request)
```

### 6.2 A2A Request Format (v1.0 spec)

```json
{
  "jsonrpc": "2.0",
  "method": "message/send",
  "id": "<uuid>",
  "params": {
    "message": {
      "role": "user",
      "parts": [{"kind": "text", "text": "query"}]
    }
  }
}
```

Required header: `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: <≥33 chars>`

### 6.3 AWS Agent URLs

All agents deployed on AWS Bedrock AgentCore runtime, `us-east-1`:
- AMFI Agent: `AMFI_AGENT_URL`
- Market Data Agent: `MARKET_DATA_AGENT_URL`
- Credit Bureau Agent: `CREDIT_BUREAU_AGENT_URL`
- Account Aggregator Agent: `ACCOUNT_AGGREGATOR_AGENT_URL`

---

## 7. API Specifications

### 7.1 MCP Tools — Voice MCP

#### `build_voice_note_script`
| Field | Type | Required | Description |
|---|---|---|---|
| `note_type` | string | ✅ | `sip_renewal` \| `meeting_schedule` \| `kyc_reminder` \| `birthday_greeting` |
| `client_name` | string | ✅ | Full client name |
| `language` | string | ❌ | BCP-47 code, default `hi-IN` |
| `script_variables` | dict | ❌ | Template fill values: `fund_name`, `expiry_date`, `rm_name` |

**Response:**
```json
{
  "note_id": "NOTE-7AAD6454",
  "message_text": "Namaste Amit Joshi ji! Cymbal Bank ki taraf se...",
  "language": "hi-IN",
  "language_name": "Hindi",
  "char_count": 142,
  "instruction": "Pass note_id to send_whatsapp_voice_note()..."
}
```

#### `send_whatsapp_voice_note`
| Field | Type | Required | Description |
|---|---|---|---|
| `mobile` | string | ✅ | E.164 format or 10-digit Indian |
| `client_name` | string | ✅ | Used for script lookup fallback |
| `note_id` | string | preferred | From `build_voice_note_script()` |
| `message_text` | string | fallback | Only if no stored script |

**Response:**
```json
{
  "status": "sent",
  "twilio_message_sid": "MMxxxx",
  "audio_url": "https://storage.googleapis.com/fsi-rm-voice-notes/voice-notes/WA-XXXX.ogg",
  "listen_url": "https://storage.googleapis.com/fsi-rm-voice-notes/voice-notes/WA-XXXX.ogg",
  "message": "Voice note sent to Amit Joshi on WhatsApp (Hindi). Twilio SID: MMxxxx. Listen: <url>"
}
```

#### `initiate_voice_call`
| Field | Type | Required | Description |
|---|---|---|---|
| `client_id` | string | ✅ | e.g. `C0022` |
| `mobile` | string | ✅ | E.164 format |
| `client_name` | string | ✅ | Full name |
| `call_type` | string | ✅ | `sip_renewal` \| `meeting_schedule` \| `kyc_reminder` \| `birthday_greeting` |
| `rm_name` | string | ✅ | Default: `Nitesh Walia` |
| `language` | string | ❌ | Default: `hi-IN` |
| `script_variables` | dict | ❌ | `fund_name`, `expiry_date`, `monthly_amount`, `proposed_date` |

---

## 8. Data Models

### 8.1 BigQuery Dataset: `<YOUR_GCP_PROJECT>.fsi_rm_poc`

**`clients` table:**
```sql
client_id       STRING  -- C0001..C0050
rm_id           STRING  -- RM001
name            STRING
phone           STRING  -- +91XXXXXXXXXX
email           STRING
segment         STRING  -- HNI | MassAffluent | SME
risk_profile    STRING  -- Conservative | Moderate | Aggressive
city            STRING
kyc_status      STRING  -- Active | PendingRenewal | Expired
kyc_expiry_date DATE
```

**`mf_holdings` table:**
```sql
client_id        STRING
scheme_name      STRING
isin             STRING
monthly_sip      FLOAT64  -- monthly SIP amount in INR
invested_amount  FLOAT64
current_value    FLOAT64
sip_expiry_date  DATE
sip_status       STRING   -- Active | Expired | Cancelled
```

**`sip_mandates` table:**
```sql
client_id        STRING
mandate_id       STRING
fund_name        STRING
monthly_amount   FLOAT64
next_debit_date  DATE
expiry_date      DATE
status           STRING
```

**`loans` table:**
```sql
client_id        STRING
loan_type        STRING   -- HomeLoan | AutoLoan | PersonalLoan | BusinessLoan
outstanding      FLOAT64
emi_amount       FLOAT64
dpd              INT64    -- Days Past Due
next_emi_date    DATE
ltv_ratio        FLOAT64
```

### 8.2 GCS Structure

```
fsi-rm-voice-notes/
  voice-notes/
    WA-{8HEX}.ogg          # WhatsApp audio (public read, Twilio fetches)

fsi-rm-call-transcripts/
  CALL-{8HEX}.json          # [{speaker, text, ts}, ...] (private)
```

### 8.3 In-Memory Call Record

```python
{
  "call_id": "CALL-BC80DD42",
  "client_id": "C0022",
  "rm_id": "RM001",
  "mobile": "+919154314766",
  "call_type": "sip_renewal",
  "language": "hi-IN",
  "language_name": "Hindi",
  "model": "gemini-3.1-flash-live-preview-04-2026",
  "initiated_at": "2026-05-29T08:41:09.123456",
  "status": "initiated",
  "twilio_call_sid": "CAxxxx",
  "outcome": null,
  "transcript": null,
  "IMPORTANT": "CALL IS NOW LIVE. Do NOT report any outcome..."
}
```

---

## 9. Voice Pipeline — End to End

### 9.1 WhatsApp Voice Note Flow

```
RM: "Send Amit Joshi a Hindi voice note about his SIP"
  │
  ▼
voice_agent → build_voice_note_script(note_type="sip_renewal", client_name="Amit Joshi")
  │             → Fills template, stores in _pending_scripts["NOTE-XXXX"]
  │             → Locks _latest_note_by_mobile["amit joshi"] = "NOTE-XXXX"
  │
  ▼ (RM approves in chat)
voice_agent → send_whatsapp_voice_note(note_id="NOTE-XXXX", mobile="+919154314766")
  │             → Fetches _pending_scripts["NOTE-XXXX"] → exact approved text
  │             → _tts_to_ogg(text, "hi-IN")
  │               → Gemini 3.1 Flash TTS → PCM 24kHz 16-bit mono
  │               → pydub: PCM → OGG/Opus
  │             → GCS upload: fsi-rm-voice-notes/voice-notes/WA-XXXX.ogg
  │             → Twilio WhatsApp: MediaUrl=GCS_URL, To=whatsapp:+919154314766
  │
  ▼
Client receives audio on WhatsApp ✅
```

### 9.2 Outbound Voice Call Flow

```
RM: "Call Amit Joshi (C0022) in Hindi about SIP renewal"
  │
  ▼ voice_agent asks for confirmation (single line)
RM: "yes"
  │
  ▼
voice_agent → initiate_voice_call(client_id="C0022", mobile="+919154314766", ...)
  │
  ├─→ POST /calls/CALL-XXXX/prepare (to liveapi-broker)
  │     Body: {language, client_name, rm_name, system_instruction (full Priya prompt)}
  │     liveapi-broker stores in CALL_CONTEXT["CALL-XXXX"]
  │
  ├─→ POST Twilio Calls.json
  │     Twilio dials +919154314766
  │     TwiML: <Connect><Stream url="wss://liveapi-broker/twilio/CALL-XXXX"/></Connect>
  │
  └─→ Returns {call_id, status="initiated", IMPORTANT="CALL IS NOW LIVE..."}

Client picks up
  │
  ▼
Twilio opens WebSocket → liveapi-broker /twilio/CALL-XXXX
  │
  ▼
liveapi-broker:
  1. Loads CALL_CONTEXT["CALL-XXXX"]
  2. Connects to Gemini Live API (gemini-3.1-flash-live-preview)
     - System instruction: full Priya prompt
     - Input/output audio transcription enabled
     - VAD: START_SENSITIVITY_HIGH, silence_duration_ms=600
  3. Sends initial turn: "(Call connected. Please begin your opening greeting.)"
  4. Priya speaks opening: "Namastey Amit ji! Main Priya hoon..."
  5. greeting_done event fires → client audio forwarded to Gemini
  6. Bidirectional audio bridge runs until Twilio stream stops
  7. Finally: _save_transcript(call_id) → GCS
```

---

## 10. Build & Deploy Pipeline

### 10.1 Build Images

**MCP servers + Gateway (`cloudbuild.yaml`):**
```yaml
steps:
  - name: gcr.io/cloud-builders/docker
    args: ['build', '-t', 'gcr.io/<YOUR_GCP_PROJECT>/fsi-rm-mcp', '-f', 'mcp_servers/Dockerfile', '.']
  - name: gcr.io/cloud-builders/docker
    args: ['build', '-t', 'gcr.io/<YOUR_GCP_PROJECT>/fsi-rm-gateway', '-f', 'gateway/Dockerfile', '.']
images:
  - gcr.io/<YOUR_GCP_PROJECT>/fsi-rm-mcp
  - gcr.io/<YOUR_GCP_PROJECT>/fsi-rm-gateway
```

**LiveAPI Broker (`cloudbuild-bridge.yaml`):**
```yaml
steps:
  - name: gcr.io/cloud-builders/docker
    args: ['build', '-t', 'gcr.io/<YOUR_GCP_PROJECT>/fsi-rm-liveapi-broker', '-f', 'bridge/Dockerfile', '.']
```

### 10.2 Deploy MCP Services

```bash
for svc in core-banking portfolio comms compliance voice; do
  gcloud run deploy fsi-rm-${svc}-mcp \
    --image gcr.io/<YOUR_GCP_PROJECT>/fsi-rm-mcp:latest \
    --region us-east1 \
    --no-allow-unauthenticated \
    --min-instances=1 \
    --set-env-vars "MCP_SERVER=${svc//-/_}_mcp,GCP_PROJECT=<YOUR_GCP_PROJECT>,..."
done
```

### 10.3 Deploy Agent Engine

```bash
python agents/orchestrator/deploy_agent.py \
  --project=<YOUR_GCP_PROJECT> \
  --location=us-east1 \
  --model=gemini-3.5-flash
```

Outputs Agent Engine ID → update `AGENT_ENGINE_ID` in gateway deploy.

---

## 11. Environment Variables Reference

### Gateway (`fsi-rm-a2a-gateway`)

| Variable | Description | Example |
|---|---|---|
| `GCP_PROJECT` | GCP project ID | `<YOUR_GCP_PROJECT>` |
| `BQ_DATASET` | BigQuery dataset | `fsi_rm_poc` |
| `AGENT_ENGINE_ID` | Reasoning Engine numeric ID | `8386758037326528512` |
| `GOOGLE_GENAI_USE_VERTEXAI` | Use Vertex AI (not AI Studio) | `True` |
| `GEMINI_MODEL` | Model for all agents | `gemini-3.5-flash` |
| `CORE_BANKING_MCP_URL` | Cloud Run URL | `https://fsi-rm-core-banking-mcp-xxx.run.app` |
| `PORTFOLIO_MCP_URL` | Cloud Run URL | `https://fsi-rm-portfolio-mcp-xxx.run.app` |
| `COMMS_MCP_URL` | Cloud Run URL | `https://...` |
| `COMPLIANCE_MCP_URL` | Cloud Run URL | `https://...` |
| `VOICE_MCP_URL` | Cloud Run URL | `https://...` |
| `MCP_INVOKER_SA_EMAIL` | Service account for MCP auth | `fsi-rm-mcp-invoker@<YOUR_GCP_PROJECT>.iam.gserviceaccount.com` |
| `AWS_ACCESS_KEY_ID` | AWS creds for AgentCore | `AKIA...` |
| `AWS_SECRET_ACCESS_KEY` | AWS creds | `...` |
| `AWS_DEFAULT_REGION` | AWS region | `us-east-1` |
| `AMFI_AGENT_URL` | AWS AgentCore invoke URL | `https://bedrock-agentcore.us-east-1.amazonaws.com/...` |
| `MARKET_DATA_AGENT_URL` | AWS AgentCore invoke URL | `https://...` |
| `CREDIT_BUREAU_AGENT_URL` | AWS AgentCore invoke URL | `https://...` |
| `ACCOUNT_AGGREGATOR_AGENT_URL` | AWS AgentCore invoke URL | `https://...` |

### Voice MCP (`fsi-rm-voice-mcp`)

| Variable | Description |
|---|---|
| `MCP_SERVER` | `voice_mcp` |
| `GCP_PROJECT` | `<YOUR_GCP_PROJECT>` |
| `LIVEAPI_BROKER_URL` | `https://fsi-rm-liveapi-broker-xxx.run.app` |
| `TWILIO_ACCOUNT_SID` | Twilio account SID |
| `TWILIO_AUTH_TOKEN` | Twilio auth token |
| `TWILIO_FROM_NUMBER` | Outbound number e.g. `+19129551733` |
| `GOOGLE_API_KEY` | Google AI Studio key (for Gemini TTS + Live) |
| `GCS_BUCKET` | `fsi-rm-voice-notes` |

### LiveAPI Broker (`fsi-rm-liveapi-broker`)

| Variable | Description |
|---|---|
| `GCP_PROJECT` | `<YOUR_GCP_PROJECT>` |
| `GCS_BUCKET` | `fsi-rm-voice-notes` (voice notes) |
| `TRANSCRIPT_BUCKET` | `fsi-rm-call-transcripts` |
| `TWILIO_AUTH_TOKEN` | For webhook signature validation |
| `GOOGLE_API_KEY` | For Gemini Live API |

---

## 12. Security Implementation

### 12.1 MCP Authentication

```
Agent → MCP Server (Cloud Run, --no-allow-unauthenticated)
  1. Agent uses impersonated service account token
     (fsi-rm-mcp-invoker@<YOUR_GCP_PROJECT>.iam.gserviceaccount.com)
  2. SA has roles/run.invoker at project level
  3. SA has roles/iam.serviceAccountTokenCreator on itself
  4. Every MCP call carries Bearer token in Authorization header
```

### 12.2 Twilio Webhook Validation

```python
def _validate_twilio(request: Request, form: dict) -> None:
    scheme = request.headers.get("x-forwarded-proto", "https")
    host = request.headers.get("host", request.url.netloc)
    url = f"{scheme}://{host}{request.url.path}"
    sig = request.headers.get("X-Twilio-Signature", "")
    if not _twilio_validator.validate(url, form, sig):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")
```

### 12.3 Data Minimization

- OTel span events (not attributes) store prompt content → can be filtered at Collector before reaching observability backend
- Call transcripts stored in private GCS bucket (no public access)
- WhatsApp audio in separate public bucket (required for Twilio media fetch)

---

## 13. Error Handling Patterns

### 13.1 MCP Tool Errors

```python
on_tool_error_callback=handle_tool_error  # all agents

def handle_tool_error(error: Exception, tool_name: str) -> str:
    return f"Tool {tool_name} failed: {str(error)[:200]}. Inform the RM and suggest retrying."
```

### 13.2 Voice Note — Graceful Defaults

```python
# sip_renewal: if monthly_amount missing, template still renders
sv.setdefault("expiry_date", "jald hi")  # "soon" if not provided
sv.setdefault("fund_name", "")           # falls back to "aapki mutual fund" in template
```

### 13.3 Bridge Prepare Must Succeed

```python
try:
    _prepare_bridge(call_id, ...)
except Exception as exc:
    return {
        **base_record,
        "status": "error",
        "error": f"Bridge /prepare failed — call not placed: {exc}"
    }
# If /prepare fails → do NOT dial Twilio (call would start with no context)
```

### 13.4 GCS Transcript — Error Visibility

```python
except Exception as exc:
    log.error("GCS transcript upload failed: %s", exc)
    raise  # re-raise so it appears in Cloud Logging (not swallowed silently)
```
