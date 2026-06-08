# Agent Teams for RM — Demo Prompts
## Gemini Enterprise Chat — Copy-Paste Ready

**RM:** Nitesh Walia (RM001)
**Demo Client:** Amit Joshi (C0022)
**Phone:** +919154314766
**Language:** Hindi

---

### Prompt 1 — Morning Intelligence Brief
```
What needs my attention this morning? Give me today's compliance alerts and clients with expiring SIPs. My RM ID is RM001.
```
**Agents:** compliance_agent + portfolio_agent
**Shows:** KYC expiries, AML flags, SIP renewals across all clients

---

### Prompt 2 — Client 360° View
```
Give me a complete profile for Amit Joshi (C0022) — account balances, portfolio, loan status, last interaction, and any pending compliance items.
```
**Agents:** client_intel_agent + portfolio_agent (parallel)
**Shows:** Full client picture from core banking, portfolio MCP, CRM

---

### Prompt 3 — Email Draft
```
Draft a personalised email to Amit Joshi about his SBI Bluechip SIP expiring in 2 days. Mention the renewal process and invite him to schedule a call with me this week.
```
**Agent:** comms_agent
**Shows:** Context-aware draft, signed as Nitesh Walia — staged for approval, not sent

---

### Prompt 4 — WhatsApp Voice Note
```
Send Amit Joshi a WhatsApp voice note in Hindi on +919154314766 reminding him his SBI Bluechip SIP expires in 2 days and we should discuss renewal.
```
**Agent:** voice_agent → Gemini 3.1 TTS → GCS → Twilio WhatsApp
**Shows:** Hindi script preview → RM approves → audio delivered to WhatsApp
**After approval:** Click the audio link in chat to play it live for the audience

---

### Prompt 5 — AI Outbound Phone Call
```
Call Amit Joshi (C0022) on +919154314766 in Hindi. His SBI Bluechip SIP is expiring in 2 days (₹5,581/month). Ask him if he wants to renew it.
```
**Agent:** voice_agent → Twilio → Gemini 3 Live API (Priya)
**Shows:** Agent asks for confirmation → Nitesh says "yes" → Twilio dials → Priya speaks in Hindi live
**During call:** Put phone on loud speaker for audience to hear
**After call:** Chat shows call ID only — no fabricated summary

---

## Confirmation Responses

When the agent asks for approval before sending/calling, type one of:
```
yes
```
```
go ahead
```
```
send it
```

---

## What to Have Ready

| Item | Detail |
|---|---|
| Gemini Enterprise | Agentspace open, new session, no prior context |
| Phone | +919154314766, charged, on loud speaker |
| WhatsApp | Open, connected to Twilio sandbox (+14155238886) |
| These prompts | On second screen or printed |

---

## Expected Timings

| Prompt | Wait time |
|---|---|
| P1 Morning brief | 20–35s |
| P2 Client profile | 15–25s |
| P3 Email draft | 10–20s |
| P4 Voice note (after approval) | 25–40s for TTS + delivery |
| P5 Call confirmation | ~5s routing, then human wait |
| P5 Call (Twilio ring) | 5–15s until client picks up |

**Total demo time: 10–15 minutes**
