# Agent Teams for Relationship Managers — Demo Strategy

## The Narrative
"Your RMs spend 70% of their day on tasks that don't need human judgment.
We built the system that gives that time back — on Google's Gemini Enterprise Agent Platform,
connected to your existing systems via MCP, coordinated via A2A, observable end-to-end,
and compliant with RBI FREE-AI by design."

## The 5-Minute Live Demo Flow

Scene 1 (0:00–1:00) → Morning Brief auto-arrives in Google Chat
Scene 2 (1:00–2:30) → RM queries client portfolio in natural language
Scene 3 (2:30–4:00) → Voice AI calls the client in Hindi (showstopper)
Scene 4 (4:00–5:00) → Observability dashboard — every trace visible

## Voice AI Use Case
The killer use case: "Call Rahul about his SIP renewal"
→ Agent calls client in Hindi via Gemini 3 Live API + Twilio
→ Live transcript streams in Chat
→ Client agrees → CRM updated → SIP renewal triggered
→ RM never touched a phone

## Tech for Voice
- Live calls: Gemini 3 Live API (11 Indian languages natively via Gemini Live)
- WhatsApp audio: Gemini 3.1 Flash TTS → OGG/Opus → Twilio WhatsApp API
- Telephony: Twilio (outbound calls via Media Streams → Gemini Live bridge)
- Orchestration: Comms Agent (ADK) manages lifecycle
- Context: Portfolio Agent feeds live SIP data into voice script before call
