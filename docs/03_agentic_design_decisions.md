# Agentic Design Document — Agent Teams for Relationship Managers

**Document Version:** 1.0  
**Date:** 2026-05-29  
**Type:** Architectural Decision Record (ADR) + Design Rationale  
**Status:** Production

---

## Table of Contents

1. [Design Philosophy](#1-design-philosophy)
2. [Decision Log](#2-decision-log)
3. [Anti-Hallucination Patterns](#3-anti-hallucination-patterns)
4. [Priya Persona Design](#4-priya-persona-design)
5. [Compliance by Design](#5-compliance-by-design)
6. [Latency Architecture](#6-latency-architecture)
7. [Known Limitations](#7-known-limitations)
8. [Future Considerations](#8-future-considerations)

---

## 1. Design Philosophy

### Core Belief: Augment the RM, Don't Replace Them

A Relationship Manager's value is in judgment — knowing when to push and when to pull back, when a client's hesitation means "I need more information" vs "I'm not interested," when a compliance issue is truly urgent vs administratively routine. That judgment cannot and should not be automated.

What can be automated: everything that happens before and after those judgment calls. Pulling data from four systems, formatting a morning brief, drafting a template email, dialling a number, logging a call note. An RM in a large Indian bank spends 70%+ of their day on these tasks.

**The design rule:** AI handles the operational layer. The RM handles the advisory layer. Every boundary between these two is a human approval gate.

### Core Belief: Trust Requires Auditability

In Indian banking, an AI system that cannot explain its actions to a regulator is a compliance liability. RBI's FREE-AI framework (2025) explicitly requires: Fairness, Reliability, Ethics, Explainability, Accountability, Inclusivity, Security.

Every design decision in this system creates an audit trail. The Memory Bank stores what the AI knew. The MCP server logs what it did. The GCS transcript records what was said. The Twilio SID proves delivery. Nothing happens off-ledger.

### Core Belief: Multi-Agent Specialization Beats Monolithic Intelligence

A single LLM trying to be simultaneously expert in KYC regulations, AMFI NAV calculations, Hindi grammar rules, and Twilio API formats will be mediocre at all of them. Five specialist agents, each with a narrow domain, a focused system prompt, and its own tool set, are more accurate, more predictable, and more independently maintainable.

---

## 2. Decision Log

---

### ADR-001: Why Multi-Agent vs Single LLM

**Context:** The initial prototype used a single large-context LLM that received all client data, all compliance rules, and all tool definitions in one prompt.

**Problem observed:**
- Context window exhaustion for RMs with 150+ clients
- Tool selection confusion (agent would call portfolio tools for compliance queries)
- Hallucination rate increased with more tools visible
- Updating one domain (e.g., new SEBI regulation) required re-testing the entire monolith

**Decision:** Five specialist sub-agents, each with one MCP server and a domain-specific system prompt. An Orchestrator routes intent to the right agent.

**Alternatives considered:**
- *Single agent with tool filtering:* Still suffers context bloat. Tool names alone consume tokens.
- *Function routing layer:* Deterministic routing (regex/classifier) loses the nuance of natural language intent detection.

**Why this is right:** An agent with 8 tools and a 500-token system prompt will outperform an agent with 40 tools and a 3,000-token system prompt on any single-domain task. The Orchestrator's routing overhead is under 3 seconds — a worthwhile tradeoff.

---

### ADR-002: Why MCP Over Direct API Integration

**Context:** Each sub-agent could call banking APIs directly via httpx. Why introduce MCP?

**Decision:** Every external system interaction goes through an MCP server.

**Reasons:**
1. **Auditability:** MCP calls are logged at the server level. Direct httpx calls from within LLM tool functions are harder to intercept and audit.
2. **Permission control:** Agent Registry can restrict which agents can call which MCP servers. A junior RM's agent cannot call loan approval APIs.
3. **Rate limiting:** MCP server can enforce per-RM rate limits without touching agent code.
4. **Reusability:** The portfolio_agent and client_intel_agent both call portfolio_mcp. Without MCP, both would need their own BigQuery connections.
5. **Banking system isolation:** Core banking never has a direct connection to an LLM. The MCP server is the trust boundary. If the LLM is compromised, it can only call MCP-exposed tools.
6. **Model Armor integration:** Agent Gateway (future) sits between agent and MCP, enabling prompt injection detection.

**Alternatives considered:**
- *Direct BigQuery calls from agents:* Bypasses auditability, creates per-agent connection management complexity.
- *REST microservices:* Equivalent capability, but loses ADK's native MCP tool discovery and listing.

---

### ADR-003: Why A2A Protocol for Cross-Cloud

**Context:** Four external data agents (AMFI, BSE/NSE, CIBIL, RBI AA) were already deployed on AWS Bedrock AgentCore. Options were: (a) migrate to GCP, (b) call via REST, (c) use A2A.

**Decision:** A2A protocol (JSON-RPC 2.0) with SigV4 authentication.

**Reasons:**
1. **Open standard:** A2A is a Linux Foundation standard (150+ orgs, April 2026). Not vendor lock-in.
2. **Agent cards:** Each AWS agent self-describes its capabilities via `/.well-known/agent-card.json`. GCP agents discover AWS agents without manual integration mapping.
3. **Auth alignment:** SigV4 is AWS's native auth mechanism. Using A2A + SigV4 means no VPN, no peering, no custom auth — just signed HTTPS.
4. **Bidirectional:** GCP agents can call AWS agents AND vice versa. When the AA agent needs real-time client data, it can call back to GCP core-banking-mcp.
5. **Avoid migration cost:** AMFI and BSE/NSE have existing Lambda-backed integrations on AWS. Migrating to GCP would take weeks and introduce regression risk.

**Implementation details learned in production:**
- Method must be `message/send` (not `tasks/send`) — A2A v1.0 spec change
- Parts format: `{kind: "text"}` (not `{type: "text"}`) — v1.0 spec change  
- Session header: `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` must be ≥33 chars

---

### ADR-004: Why Gemini 3.5 Flash for Orchestration and Sub-Agents

**Context:** Model selection for orchestration and sub-agents.

**Decision:** `gemini-3.5-flash` for all orchestration and sub-agent reasoning.

**Reasons:**
1. **Latency:** Flash delivers routing decisions in 2-4 seconds. Gemini 3.1 Pro adds 6-10 seconds per routing hop.
2. **Cost:** At 150 clients × multiple daily interactions, Pro would increase token costs ~8x.
3. **Hindi instruction following:** Flash has strong multilingual instruction compliance — critical for the voice agent that constructs Hindi script variables.
4. **Tool calling:** Flash supports parallel function call generation (multiple `FunctionCall` parts in one response).

**Alternatives considered:**
- *Gemini 3.1 Pro for orchestrator, Flash for sub-agents:* Tested; Pro adds latency without material improvement in routing accuracy for this use case.
- *Gemini 3.1 Flash (smaller):* Lower context window; insufficient for clients with long CRM history.

---

### ADR-005: Why Gemini 3 Live API for Voice Calls

**Context:** Options for real-time voice conversation: Sarvam AI, Amazon Polly + Lex, Deepgram + GPT-4, Gemini Live.

**Decision:** Gemini 3 Live API (`gemini-3.1-flash-live-preview`) via Twilio Media Streams.

**Reasons:**
1. **End-to-end:** Single API handles STT → reasoning → TTS in one session. Alternatives require STT → LLM → TTS with inter-service latency at every boundary.
2. **Hindi fluency:** Gemini Live's Hindi is natural and contextually aware. It handles Hinglish code-mixing (common in Indian client calls) without explicit configuration.
3. **Barge-in handling:** Gemini Live natively detects when the client interrupts and stops generating. Most alternatives require custom VAD + interrupt logic.
4. **Conversation continuity:** The same session maintains context across the entire call. A REST-based approach loses context unless explicitly managed.
5. **System instruction:** The Priya persona, call structure, and agenda are injected once at session start. They persist for the entire call without reinjection.

**Sarvam AI — why not:**
- Sarvam is purpose-built for Indian languages but is a separate STT + TTS stack, not an end-to-end reasoning model.
- Would require a custom LLM integration layer for the conversation logic.
- Gemini Live with Hindi system prompt achieves the same language quality with simpler architecture.

**Key implementation insight:** Gemini Live outputs PCM at 24kHz. Twilio expects μ-law at 8kHz. The broker handles the conversion chain: PCM 24kHz → ratecv → PCM 8kHz → lin2ulaw → Twilio. Getting this wrong (using 16kHz instead of 24kHz) caused audio playing at 67% speed — a subtle bug discovered in production.

---

### ADR-006: Why Gemini 3.1 Flash TTS for WhatsApp Voice Notes (Not Cloud TTS)

**Context:** Original implementation used Google Cloud TTS Neural2 voices. Migrated to Gemini 3.1 Flash TTS.

**Decision:** `gemini-3.1-flash-tts-preview` with Autonoe voice.

**Reasons:**
1. **Natural prosody:** Cloud TTS Neural2 sounds professional but slightly robotic. Gemini TTS sounds conversational — closer to a real person.
2. **Director's Notes:** Natural language style control. `[warmly]`, `[clearly]` audio tags and a `### DIRECTOR'S NOTES` prefix control accent, pacing, and warmth without phoneme-level tuning.
3. **Accent control:** Cloud TTS has a fixed Indian English accent (or Hindi). Gemini TTS can be instructed: "Natural urban Indian cadence, clear and professional" — producing a more authentic Cymbal Bank assistant voice.
4. **Single voice for all languages:** Autonoe handles Hindi, English, and Indian regional languages with the same voice identity — the client hears a consistent "Priya" across all communications.

**Tradeoff accepted:** Gemini TTS takes 10-35 seconds for a typical voice note. Cloud TTS takes ~400ms. For WhatsApp delivery (asynchronous), 30 seconds is acceptable. For live calls, Gemini Live API is used instead.

**Format decision:** Cloud TTS outputs OGG Opus directly. Gemini TTS outputs raw PCM. Added `pydub` + `ffmpeg` to convert PCM 24kHz → OGG/Opus for WhatsApp compatibility.

---

### ADR-007: Why Voice Note Script Locking (note_id Pattern)

**Context:** Voice notes were hallucinating wrong fund names, wrong amounts, wrong languages despite correct prompts.

**Root cause identified:** The voice agent builds the script in Session 1 (initial prompt). The RM approves in Session 2 ("yes"). By Session 2, the agent opens a new MCP connection and calls `build_voice_note_script()` again — this time with parameters from Memory Bank (previous sessions), which could be from a completely different client or conversation.

Observed example:
```
Session 1: RM asks "Send Amit Joshi a voice note about SBI Bluechip SIP expiring in 2 days"
           Agent builds: "Aapka SBI Bluechip SIP 2 din mein expire ho raha hai"
Session 2: RM says "yes"
           Agent rebuilds from Memory Bank: "Aapka HDFC Mid-Cap SIP 28 May 2026..."
           → Wrong fund, wrong date, sent to client
```

**Decision:** Server-side script locking with first-build-wins semantics.

**Implementation:**
```python
# build_voice_note_script():
if client_name.lower() not in _latest_note_by_mobile:
    _pending_scripts[note_id] = {"message_text": text, ...}
    _latest_note_by_mobile[client_name.lower()] = note_id
    # First build → stored
else:
    # Subsequent build → discarded entirely (not even stored in _pending_scripts)
    # Agent cannot use it even if it passes the note_id explicitly

# send_whatsapp_voice_note():
# Agent's note_id parameter ignored if not in _pending_scripts
# Falls back to _latest_note_by_mobile[client_name.lower()]
# → Always uses the first approved script
```

**Why first-build-wins (not most-recent):** The first build happens when the RM's prompt is freshest and most specific. Subsequent builds happen after sessions contaminate Memory Bank with older context. The first build is always the most accurate.

**Why discard (not just deprioritize):** If subsequent builds are stored in `_pending_scripts`, the agent can still pass their `note_id` and bypass the lock. Discarding prevents any path to the wrong script.

---

### ADR-008: Why Transcript Flush-at-Disconnect

**Context:** Call transcripts were empty (0 segments) for completed calls.

**Root cause:** Gemini Live sends transcription in chunks; `turn_complete` signals when a full turn is done. When a client hangs up, Twilio closes the WebSocket. The `turn_complete` for the last exchange may not arrive before the WebSocket closes — leaving `_last_user_text` and `_last_gemini_text` non-empty but unflushed.

**Decision:** Add `finally` block in `_gemini_to_twilio` to flush any remaining text.

```python
async def _gemini_to_twilio(...):
    _last_user_text = ""
    _last_gemini_text = ""
    try:
        # ... main streaming loop
    except Exception as e:
        log.error("Gemini Live error: %s", e)
    finally:
        # Fires even if WebSocket closed before turn_complete
        if (_last_user_text or _last_gemini_text) and call_id in TRANSCRIPTS:
            TRANSCRIPTS[call_id].append({"speaker": "client", "text": _last_user_text.strip()})
            TRANSCRIPTS[call_id].append({"speaker": "priya",  "text": _last_gemini_text.strip()})
```

**Also fixed:** Changed `_last_gemini_text = chunk` (overwrite) to `_last_gemini_text += chunk` (concatenate). Gemini streams text in 1-3 word chunks. Without concatenation, only the last chunk of each turn was saved ("hain?" instead of "Namastey Amit ji! Main Priya hoon... Aap kaise hain?").

---

### ADR-009: Why max-instances=1 on LiveAPI Broker

**Context:** Cloud Run scales horizontally by default.

**Problem:** `CALL_CONTEXT` is an in-memory Python dict. When the voice_mcp calls `POST /calls/{call_id}/prepare` and then Twilio connects to `WebSocket /twilio/{call_id}`, these two requests may hit different Cloud Run instances. The WebSocket handler would find no context for the call_id and fall back to a generic (wrong) system instruction.

**Decision:** `--max-instances=1` on fsi-rm-liveapi-broker.

**Implication:** The broker is a stateful singleton. It cannot horizontally scale without migrating `CALL_CONTEXT` to a shared store (Redis, Firestore). For a PoC handling one call at a time, single instance is sufficient.

**Future mitigation:** Replace `CALL_CONTEXT` dict with Firestore document (TTL: 30 minutes). Allows safe horizontal scaling.

---

### ADR-010: Why min-instances=1 on All Services

**Context:** Cloud Run scales to zero by default.

**Problem:** Cold start for a Python Cloud Run service with ADK + google-genai dependencies is 6-15 seconds. In a demo context (and in production), an RM asking a question and waiting 15 seconds for the first response is unacceptable.

**Decision:** `--min-instances=1` on all 7 Cloud Run services.

**Cost:** Approximately $15-25/month per service to keep warm (0.083 vCPU-hours × 24h × 30 days × $0.00002/vCPU-hour × per service). Total system warm cost: ~$150/month. Acceptable for the latency gain.

**Observed improvement:** Routing from gateway to first MCP response improved from 8-15 seconds (cold) to 3-5 seconds (warm).

---

### ADR-011: Why Two GCS Buckets

**Context:** Original design stored voice notes and transcripts in the same bucket.

**Problem:**
- WhatsApp audio files in `fsi-rm-voice-notes` need **public read** — Twilio fetches the audio from GCS to send to WhatsApp. The URL must be publicly accessible.
- Call transcripts in `fsi-rm-call-transcripts` must be **private** — they contain verbatim client conversations, PII, and are compliance records.

If both types were in one bucket, either the bucket would be over-permissioned (public transcripts), or Twilio wouldn't be able to fetch the audio (private audio).

**Decision:** Two buckets with different IAM policies.

| Bucket | Access | Contents |
|---|---|---|
| `fsi-rm-voice-notes` | allUsers objectViewer | WhatsApp OGG audio |
| `fsi-rm-call-transcripts` | Private (service accounts only) | Call transcripts JSON |

---

### ADR-012: Why Human-in-Loop for All Outbound Communications

**Context:** Could the system auto-send communications without RM confirmation?

**Decision:** No. Every outbound communication requires explicit RM approval.

**Regulatory basis:**
- **RBI FREE-AI framework (Aug 2025):** "Human oversight of AI decisions affecting customers"
- **DPDP Act 2023:** Client data processing for communications requires purposeful consent
- **RBI Fair Practices Code:** Outbound calls must be authorized by the financial institution's representative
- **SEBI IA Regulations:** Investment-adjacent communications must be RM-reviewed

**Implementation:** The voice agent shows script/call details and asks "Shall I proceed?" The comms agent surfaces a draft and says "please confirm before I send." The actual send tool is only called after RM says yes.

**UX impact:** Adds one conversational turn (10-35 seconds human wait) per action. Accepted as necessary for compliance and trust-building.

---

### ADR-013: Why BigQuery Over Relational Database

**Context:** Could have used Cloud SQL (PostgreSQL) for client data.

**Decision:** BigQuery for all client data.

**Reasons:**
1. **Analytics-first queries:** Morning brief requires: GROUP BY, date arithmetic across all clients, SUM of overdue amounts. These are OLAP queries, not OLTP.
2. **Serverless:** No connection pooling management. MCP servers are stateless Cloud Run instances; BigQuery handles connection management.
3. **BI integration:** Portfolio dashboards via Looker Studio connect natively to BigQuery.
4. **Storage cost:** BigQuery active storage ($0.02/GB/month) vs Cloud SQL ($0.17/GB/month) — significant at scale.
5. **ML-ready:** BigQuery ML enables future next-best-action models trained directly on transaction history.

**Tradeoff:** BigQuery cold query latency (~1-2 seconds for cached results). Cloud SQL would be faster for single-row lookups. For an RM assistant where 2-second tool latency is acceptable, this is fine.

---

## 3. Anti-Hallucination Patterns

The system applies five patterns to prevent LLM hallucination:

### Pattern 1: Tool-Grounded Responses
Every agent system prompt contains: *"You MUST call your MCP tools to perform any action. NEVER fabricate tool results."* Agents cannot respond with client data, portfolio figures, or delivery confirmations without a corresponding tool call returning real data.

### Pattern 2: Server-Side Script Locking
Voice note scripts are stored server-side by `build_voice_note_script()`. The agent passes a `note_id` reference — it cannot alter the script text between approval and delivery. First-build-wins semantics prevent Memory Bank contamination from overwriting the approved script.

### Pattern 3: `IMPORTANT` Field in Tool Response
`initiate_voice_call()` returns an `IMPORTANT` field:
```
"CALL IS NOW LIVE. Do NOT report any outcome, transcript, SIP confirmation,
KYC actions, CRM logs, or next steps — the call has just started and none
of that information exists yet."
```
The LLM reads this field and cannot fabricate a call summary.

### Pattern 4: Relative Time Expressions
Voice note templates use `{expiry_date}` as a direct passthrough. Voice agent system prompt explicitly says: *"expiry_date — PASS THE EXACT PHRASE THE RM USED. Never convert to a calendar date. RM said '2 days' → expiry_date: '2 din mein'"*

Prevents agent from calculating a calendar date that may be wrong.

### Pattern 5: No-Hallucination Confirmation Format
Voice call Step 1 says: *"Ask RM to confirm with ONE short line only — no plans, no steps, no explanations: 'Ready to call [client] at [mobile] about [topic]. Shall I proceed?'"* Prevents the agent from outputting a verbose "proposed call flow" instead of asking for simple confirmation.

---

## 4. Priya Persona Design

### Gender Rule at the Top

The gender rule (`⚠️ GENDER RULE — HINDI: You are FEMALE. ALWAYS: rahi hoon, karUNGI, bataDUNGI`) is the first line of Priya's system instruction. Gemini Live, as a real-time streaming model, attends most strongly to the beginning of the system prompt. Placing the gender rule later in the prompt resulted in occasional masculine verb forms ("batadunga"). Placing it first eliminated the issue.

### First Name Only

*"Use first name only with ji throughout the call. Client: 'Amit ji'. RM: 'Nitesh ji'. Examples: 'Amit ji', 'Rekha ji'. NEVER use full name after the opening."*

Rationale: Indian conversational norms use first-name address with "ji" for respect. "Amit Joshi ji" in every sentence sounds robotic. "Amit ji" is natural.

### Never Commits on Behalf of the Bank

*"Never say you will process, renew, or execute any action — only that you will inform {rm_name} ji."*

This is both a compliance requirement (Priya has no authority to commit the bank to anything) and a trust mechanism (clients should know the RM makes all decisions).

### 5-Step Call Structure

The call follows a strict structure: Opening → Agenda → Listen → Handle → Close. This structure:
1. Ensures Priya doesn't skip the agenda and drift into general conversation
2. Provides a predictable flow the client can anticipate
3. Creates a transcript that compliance teams can parse systematically

### "Namastey" as Universal Opening

Originally, the opening greeting was language-dependent (`{greeting}` from SUPPORTED_LANGUAGES dict). Changed to always "Namastey" after observing Priya sometimes spoke the instruction text "(Use greeting for this language)" verbatim. Hardcoding "Namastey" eliminates ambiguity — it is universally understood across Indian languages.

---

## 5. Compliance by Design

| RBI FREE-AI Sutra | Implementation |
|---|---|
| **Fairness** | Same agent quality for all clients; no segment-based tool access differentiation |
| **Reliability** | min-instances=1 eliminates cold-start failures; error callbacks surface tool failures |
| **Ethics** | Human-in-loop for all outbound; Model Armor (future) blocks manipulative outputs |
| **Explainability** | Every agent action logged in Cloud Logging with tool call details and parameters |
| **Accountability** | GCS transcript = permanent call record; Twilio SID = delivery proof; BigQuery = audit trail |
| **Inclusivity** | 11 Indian languages in Gemini Live; Hindi-first voice notes; WhatsApp-native delivery |
| **Security** | VPC Service Controls (future); MCP OAuth 2.1; SigV4 cross-cloud; private transcript bucket |

---

## 6. Latency Architecture

### Latency Budget (warm instances, no human wait)

| Phase | Target | Actual |
|---|---|---|
| Gemini Enterprise → A2A Gateway | <1s | ~30ms |
| Gateway → Agent routing | <5s | 3-5s (LLM inference) |
| MCP tool execution | <5s | 1-20s (depends on query) |
| Gemini TTS generation | <20s | 10-35s |
| GCS upload | <2s | ~0.5s |
| Twilio WhatsApp dispatch | <2s | ~0.8s |

### Biggest Latency Opportunities

1. **Gemini TTS (10-35s):** Most variable. Could cache common scripts (SIP renewal, KYC reminder) at session start. First call generates and caches; subsequent calls to same client type use cache.

2. **Agent routing (3-5s):** Determined by orchestrator LLM inference. Cannot be meaningfully reduced without changing model. Using Flash (not Pro) already optimizes this.

3. **Sequential agent calls:** When compliance_agent and portfolio_agent are both needed (morning brief), they currently run sequentially. ADK's `ParallelAgent` workflow would run them concurrently, saving the slower agent's time (est. 8-10 seconds).

---

## 7. Known Limitations

| Limitation | Impact | Mitigation |
|---|---|---|
| LiveAPI Broker max-instances=1 | Cannot handle concurrent calls | Move CALL_CONTEXT to Firestore for stateless horizontal scaling |
| Memory Bank contamination | Old session data can pollute new session script variables | Voice note locking (implemented); future: session isolation via Memory Bank scoping |
| Gemini TTS latency (10-35s) | WhatsApp voice note takes 40-60s end-to-end | Pre-generate common scripts at session start; cache in GCS |
| Sequential orchestrator routing | Morning brief takes 35-40s (compliance + portfolio sequential) | Implement ADK ParallelAgent for morning brief workflow |
| In-memory call store | Calls lost if liveapi-broker restarts mid-call | Persist call records to Firestore with TTL |
| No real CBS integration | Uses BigQuery synthetic data | Implement core-banking-mcp with actual Finacle/BaNCS API behind MCP boundary |
| Expiry date hallucination | Agent sometimes converts "2 days" → "May 31, 2026" | Partial fix via prompt; complete fix: validate expiry_date is relative expression before storing in script |

---

## 8. Future Considerations

### Near-Term (3 months)

1. **ADK ParallelAgent for morning brief:** Run compliance_agent and portfolio_agent concurrently. Estimated saving: 8-10 seconds per morning brief.

2. **Pre-generated script cache:** For known call types (SIP renewal, KYC reminder), generate and cache TTS audio when the RM opens their session. Zero TTS latency when RM actually places the call.

3. **Firestore CALL_CONTEXT:** Replace in-memory dict. Enables liveapi-broker to scale to multiple instances for concurrent calls.

4. **Real CBS integration:** Replace BigQuery synthetic data with MCP server calling Finacle/BaNCS APIs. The MCP boundary means zero agent code changes required.

### Medium-Term (6 months)

5. **Account Aggregator (RBI AA):** Fully productionize the AA agent. With client consent, show holdings across HDFC, Axis, SBI — not just Cymbal Bank. Single biggest RM competitive advantage.

6. **Memory Bank enrichment:** After each client call, summarize the outcome and store in Memory Bank. RM's morning brief then references "Amit confirmed renewal last Thursday" automatically.

7. **Next-Best-Action model:** BigQuery ML trained on successful vs unsuccessful RM interventions to recommend the optimal action for each client situation.

8. **Agent Observability dashboard:** Token burn rate per RM, tool call failure rates, human approval rate (% of drafts sent unchanged), call success rate — all in Cloud Monitoring.

### Long-Term (12 months)

9. **Multi-RM deployment:** Currently hardcoded for Nitesh Walia (RM001). Generalize to support any RM ID, with per-RM agent configuration, permission sets, and Memory Bank isolation.

10. **Agent Gateway + Model Armor:** Enable prompt injection protection for all MCP calls. Block attempts to use the RM assistant for non-banking purposes.

11. **Compliance co-pilot:** Dedicated agent that proactively monitors regulatory changes (RBI/SEBI circulars), maps them to affected clients, and generates action items for the RM.
