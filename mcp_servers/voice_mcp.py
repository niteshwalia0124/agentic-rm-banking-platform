"""
Voice MCP Server — outbound voice calls + WhatsApp voice notes.

Two delivery modes:
  1. WhatsApp Voice Note  → send_whatsapp_voice_note()
       Gemini 3.1 Flash TTS (Autonoe voice) → PCM → OGG/Opus → GCS → Twilio WhatsApp API
       No pipecat/bridge needed. Works today with sandbox credentials.

  2. Interactive Voice Call → initiate_voice_call()
       Twilio dials client → Media Streams WebSocket → Pipecat bridge
         → Gemini Live API (real-time multilingual conversation)
       Requires PIPECAT_BRIDGE_URL to be deployed.

In PoC mode (no TWILIO_ACCOUNT_SID), both tools return simulated records.

Run locally:  python mcp_servers/voice_mcp.py
Deploy:       gcloud run deploy voice-mcp --source .
"""

import io
import logging
import os
import uuid
from datetime import datetime

import httpx
from mcp.server.fastmcp import FastMCP

log = logging.getLogger("voice_mcp")
from mcp.server.transport_security import TransportSecuritySettings

BQ_PROJECT = os.getenv("GCP_PROJECT", "your-project")
BQ_DATASET = os.getenv("BQ_DATASET", "fsi_rm_poc")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")
# WhatsApp sandbox / business number  e.g. "whatsapp:+14155238886"
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

GCS_BUCKET = os.getenv("GCS_BUCKET", "fsi-rm-voice-notes")

GEMINI_LIVE_MODEL = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live-preview-04-2026")

LIVEAPI_BROKER_URL = os.getenv("LIVEAPI_BROKER_URL", "http://localhost:8010")

_no_dns_rebinding = TransportSecuritySettings(enable_dns_rebinding_protection=False)
mcp = FastMCP("voice-mcp", transport_security=_no_dns_rebinding)

# In-memory call store for PoC (replace with BigQuery in production)
_calls: dict[str, dict] = {}

# Pending voice note scripts — keyed by note_id, set by build_voice_note_script()
# send_whatsapp_voice_note() fetches from here so the agent cannot alter the text
_pending_scripts: dict[str, dict] = {}

# Latest note_id per mobile number — so send can find the most recent approved script
# even if the agent forgets the note_id across sessions
_latest_note_by_mobile: dict[str, str] = {}

# BCP-47 language codes supported by Gemini Live API
# Gemini Live supports all major Indian languages natively
SUPPORTED_LANGUAGES: dict[str, dict] = {
    "hi-IN": {"name": "Hindi",      "greeting": "Namaste",    "closing": "Dhanyavaad"},
    "ta-IN": {"name": "Tamil",      "greeting": "Vanakkam",   "closing": "Nandri"},
    "te-IN": {"name": "Telugu",     "greeting": "Namaskaram", "closing": "Dhanyavaadalu"},
    "kn-IN": {"name": "Kannada",    "greeting": "Namaskara",  "closing": "Dhanyavadagalu"},
    "ml-IN": {"name": "Malayalam",  "greeting": "Namaskaram", "closing": "Nandri"},
    "mr-IN": {"name": "Marathi",    "greeting": "Namaskar",   "closing": "Dhanyavad"},
    "bn-IN": {"name": "Bengali",    "greeting": "Namaskar",   "closing": "Dhanyabad"},
    "gu-IN": {"name": "Gujarati",   "greeting": "Kem cho",    "closing": "Aabhar"},
    "pa-IN": {"name": "Punjabi",    "greeting": "Sat Sri Akal","closing": "Shukriya"},
    "or-IN": {"name": "Odia",       "greeting": "Namaskar",   "closing": "Dhanyabad"},
    "as-IN": {"name": "Assamese",   "greeting": "Namaskar",   "closing": "Dhanyabad"},
    "en-IN": {"name": "English",    "greeting": "Hello",      "closing": "Thank you"},
}

# Script templates use {language_instruction} and {greeting} so the same
# template works for every language — Gemini Live handles fluent speech in
# whichever language is specified in the language_instruction.
# ── Agenda sentences — what Priya tells the client (not system prompts) ───────
# These are filled with script_variables and become the call agenda.
# The full system instruction is built by _build_system_instruction() below.
AGENDAS: dict[str, str] = {
    "sip_renewal": (
        "Aapka {fund_name} SIP, jo ₹{monthly_amount} per month hai, "
        "{expiry_date} expire ho raha hai. "
        "{rm_name} ji ne mujhe aapko yeh jaankari dene aur poochhne ke liye kaha hai — "
        "kya aap is SIP ko renew karna chahenge?"
    ),
    "meeting_schedule": (
        "{rm_name} ji aapke saath ek portfolio review meeting schedule karna chahte hain "
        "{proposed_date} ko. Kya aap us samay available honge? "
        "Yeh ek 30-minute ka Google Meet session hoga."
    ),
    "kyc_reminder": (
        "Aapka KYC {expiry_date} ko expire ho raha hai. "
        "Account fully active rakhne ke liye aapko updated Aadhaar aur PAN submit karna hoga — "
        "branch pe ya net banking portal ke zariye. "
        "{rm_name} ji chahte hain ki aap yeh jaldi complete kar lein."
    ),
    "birthday_greeting": (
        "{rm_name} ji ne aapko Cymbal Bank ki poori team ki taraf se "
        "bahut bahut badhai dene ke liye kaha hai aapke iss khaas din par. "
        "Woh chahte hain ki aapka din bahut achha ho."
    ),
}

# Keep SCRIPTS as an alias so callers using call_type still work
SCRIPTS = AGENDAS


def _build_system_instruction(
    agenda: str,
    client_name: str,
    rm_name: str,
    language_instruction: str,
    greeting: str,
    closing: str,
) -> str:
    """Build the unified Priya call system instruction for any call type."""
    client_first = client_name.split()[0]
    rm_first = rm_name.split()[0]
    return f"""⚠️ GENDER — FIRST AND MOST IMPORTANT RULE:
You are FEMALE. In EVERY sentence without exception use feminine Hindi verb forms:
✅ ALWAYS: rahi hoon · karUNGI · bataDUNGI · chahTI hoon · bolUNGI · dUNGI · milEGI · karwaDUNGI
❌ NEVER:  raha hoon · karunga · batadunga · chahta hoon · bolunga · dunga · milega · karwadunga
Same feminine agreement in Tamil, Telugu, Kannada, Marathi, Bengali, Punjabi — every language, every sentence.

You are Priya, a female AI voice assistant for {rm_name}, Relationship Manager at Cymbal Bank.

━━━ HOW TO ADDRESS THE CLIENT ━━━
- Infer the client's gender from their name ({client_name}).
- Male clients: address as "Sir" in English, "ji" after first name in Hindi/Indian languages.
- Female clients: address as "Ma'am" in English, "ji" after first name in Hindi/Indian languages.
- Use FIRST NAME ONLY with ji throughout the call. Client: "{client_first} ji". RM: "{rm_first} ji".
  Examples: "Amit ji", "Rekha ji", "Arjun ji". NEVER use full name after the opening.
- Use the client's first name ({client_first}) ONLY TWICE in the entire call:
    1. In the opening greeting (Step 1)
    2. In the closing thank-you (Step 5)
- For ALL other sentences, address them as "Sir" or "Ma'am" (or language equivalent).
  NEVER repeat the name mid-conversation — it sounds robotic.

{language_instruction}

━━━ CALL STRUCTURE — follow these 5 steps exactly, in order ━━━

STEP 1 — OPENING:
Say this first, warmly:
"Namastey {client_first} ji! Main Priya hoon — {rm_first} ji ki AI assistant, Cymbal Bank se bol rahi hoon. Aap kaise hain?"
Wait for the client to respond before continuing.

STEP 2 — STATE THE AGENDA:
Say this immediately after the client acknowledges, in the call language:
"{rm_first} ji ne mujhe aapko ek khaas baat ke liye call karne ke liye kaha tha: {agenda}"
Then pause and listen. Do NOT say anything else — wait for the client to respond.

STEP 3 — LISTEN AND NOTE:
- Listen carefully to the client's full response before replying. Acknowledge whatever they say.
- SPECIAL CASE — if the client asks to schedule a call or meeting with {rm_first} ji:
  Ask them: "Zaroor Sir/Ma'am! Aapko kaunsa din aur time convenient rahega?"
  Listen to their response. Once they give a time/day:
    - Confirm it back: "Main aapki meeting schedule kar dungi — aapko humari taraf se jald hi confirmation milegi."
    - If the client gives multiple options (e.g. "Monday or Tuesday"), confirm all of them:
      "Main {rm_first} ji ko bataungi ki aap [day1] ya [day2] ko available hain — woh confirm karenge."
    - If client says "anytime" or "you decide": say "Main {rm_first} ji se poochh ke aapko
      confirm karwa dungi."
  Note the agreed time as part of your update to {rm_first} ji and move to Step 5.
- If the client raises a different topic (loans, other accounts, market news):
  Say "{rm_first} ji uss baare mein aapko personally baat karenge, Sir/Ma'am. Mein unhe update kar dungi."
- If the client is busy or says this is a bad time:
  Say "Bilkul Sir/Ma'am, koi baat nahi. Mujhe apna preferred samay bata dijiye — main usi samay call kar lungi." and go to Step 5.

STEP 4 — HANDLE THE CLIENT'S RESPONSE:
- If client AGREES / says YES: acknowledge warmly ("Bahut achha Sir/Ma'am") and confirm you will inform {rm_first} ji.
- If client DECLINES / says NO or wants to cancel: acknowledge without judgment.
  Say "Bilkul Sir/Ma'am, main {rm_first} ji ko aapka decision bata dungi." Move to Step 5.
  Do NOT push, persuade, or re-open the topic.
- If client is UNSURE or has conditions (e.g., "I need to review first"): acknowledge their reasoning.
  Say "Bilkul samajh gai, Sir/Ma'am. Main {rm_first} ji ko aapki sari baat bata dungi
  — [summarise what client said in one line]." Move to Step 5.
- If client asks you to DO something (schedule meeting, cancel SIP, send details):
  Acknowledge warmly and say you will pass it to {rm_first} ji. Never commit to executing
  the action yourself. Example: "Zaroor Sir, main yeh {rm_first} ji tak pahuncha dungi."

STEP 5 — CLOSING (end EVERY call with this, in the call language):
Briefly summarise what you will pass to {rm_first} ji (agenda outcome + any requests client made),
then close with:
"Main {rm_first} ji ko hamaari poori baatcheet ke baare mein update kar dungi.
Aapka bahut bahut shukriya {client_first} ji. {closing}!"

━━━ WHAT YOU NEVER DO ━━━
- Never discuss anything outside the agenda stated in Step 2.
- Never collect account numbers, PINs, OTPs, passwords, or any sensitive data.
- Never make commitments on behalf of the bank or {rm_first} ji.
- Never say you will process, renew, or execute any action — only that you will inform {rm_first} ji.
- Never try to sell, upsell, or mention any product not in the agenda.
- If asked "are you AI?", say honestly: "Haan, main ek AI assistant hoon jo {rm_first} ji ki taraf se kaam karti hoon."
"""


def _build_language_instruction(language_code: str) -> str:
    """Build the language instruction line injected into every script."""
    lang = SUPPORTED_LANGUAGES.get(language_code, SUPPORTED_LANGUAGES["hi-IN"])
    lang_name = lang["name"]
    if language_code == "en-IN":
        return "Speak in clear Indian English. Be professional and warm."
    return (
        f"Speak in {lang_name}. "
        f"If the client switches to another language or English, adapt naturally (code-mixing is fine). "
        f"Use the {lang_name} script naturally — do not transliterate robotically."
    )


def _resolve_language(language: str) -> str:
    """Accept BCP-47 code or plain name and return a valid BCP-47 code."""
    if language in SUPPORTED_LANGUAGES:
        return language
    # Try matching by display name (case-insensitive)
    for code, info in SUPPORTED_LANGUAGES.items():
        if info["name"].lower() == language.lower():
            return code
    # Fallback: Hindi
    return "hi-IN"


@mcp.tool()
def list_supported_languages() -> dict:
    """List all Indian languages supported for voice calls."""
    return {
        "supported_languages": [
            {"code": code, "name": info["name"]}
            for code, info in SUPPORTED_LANGUAGES.items()
        ],
        "model": GEMINI_LIVE_MODEL,
        "note": "Gemini Live API handles fluent speech in all listed languages natively.",
    }


@mcp.tool()
def initiate_voice_call(
    client_id: str,
    mobile: str,
    client_name: str,
    call_type: str,
    script_variables: dict,
    rm_id: str = "RM001",
    rm_name: str = "Nitesh Walia",
    language: str = "hi-IN",
) -> dict:
    """
    Stage and (if Twilio configured) initiate an outbound voice call to a client
    in the client's preferred regional language.

    call_type: sip_renewal | meeting_schedule | kyc_reminder | birthday_greeting
    rm_name: Full name of the Relationship Manager on whose behalf Priya is calling.
             e.g. "Nitesh Walia". Always pass this explicitly.
    language: BCP-47 code OR language name. Examples:
      "hi-IN" or "Hindi", "ta-IN" or "Tamil", "te-IN" or "Telugu",
      "kn-IN" or "Kannada", "ml-IN" or "Malayalam", "mr-IN" or "Marathi",
      "bn-IN" or "Bengali", "gu-IN" or "Gujarati", "pa-IN" or "Punjabi",
      "en-IN" or "English"
    script_variables: dict of template values, e.g.:
      {"fund_name": "HDFC Mid-Cap", "monthly_amount": "25,000", "expiry_date": "28 May 2026"}

    In PoC mode (no TWILIO_ACCOUNT_SID), returns a simulated call record.
    """
    if call_type not in SCRIPTS:
        return {
            "error": f"Unknown call_type '{call_type}'. Valid: {list(SCRIPTS.keys())}"
        }

    resolved_lang = _resolve_language(language)
    lang_info = SUPPORTED_LANGUAGES[resolved_lang]
    call_id = f"CALL-{uuid.uuid4().hex[:8].upper()}"

    # Fill script_variables into the agenda sentence
    lang_instruction = _build_language_instruction(resolved_lang)
    sv = dict(script_variables)
    # Graceful defaults so the agenda is always readable
    if call_type == "sip_renewal":
        fund = sv.get("fund_name", "").strip()
        sv["fund_name"] = fund if fund else "aapki mutual fund"
        sv.setdefault("monthly_amount", "")
        sv.setdefault("expiry_date", "jald hi")
    sv.setdefault("proposed_date", "")
    agenda_vars = {"rm_name": rm_name, "client_name": client_name, **sv}
    try:
        agenda_filled = AGENDAS[call_type].format(**agenda_vars)
        missing_var = None
    except KeyError as e:
        agenda_filled = AGENDAS[call_type]
        missing_var = str(e)

    # Build the full unified system instruction wrapping the agenda
    script_filled = _build_system_instruction(
        agenda=agenda_filled,
        client_name=client_name,
        rm_name=rm_name,
        language_instruction=lang_instruction,
        greeting=lang_info["greeting"],
        closing=lang_info["closing"],
    )

    base_record = {
        "call_id": call_id,
        "client_id": client_id,
        "rm_id": rm_id,
        "mobile": mobile,
        "call_type": call_type,
        "language": resolved_lang,
        "language_name": lang_info["name"],
        "script_preview": script_filled[:300],
        "model": GEMINI_LIVE_MODEL,
        "initiated_at": datetime.utcnow().isoformat(),
        "outcome": None,
        "transcript": None,
    }
    if missing_var:
        base_record["warning"] = f"Script variable {missing_var} not provided"

    # PoC simulation mode — no real Twilio credentials
    if not TWILIO_ACCOUNT_SID:
        base_record["status"] = "simulated"
        base_record["note"] = (
            f"PoC simulation — {lang_info['name']} call to {client_name} staged. "
            "Set TWILIO_ACCOUNT_SID to place real calls. "
            "Real flow: Twilio dials client → Media Streams → Pipecat → Gemini Live API."
        )
        _calls[call_id] = base_record
        return base_record

    # Prepare broker context, then dial via Twilio.
    # If /prepare fails we must NOT proceed — the call would launch with empty context
    # (wrong/missing language and client name in the Gemini Live system instruction).
    try:
        _prepare_bridge(
            call_id=call_id,
            language=resolved_lang,
            client_name=client_name,
            rm_name=rm_name,
            rm_message=script_filled,
            script_variables=script_variables,
        )
    except Exception as exc:
        return {
            **base_record,
            "status": "error",
            "error": f"Bridge /prepare failed — call not placed: {exc}",
        }

    twilio_call_sid = _place_twilio_call(call_id, mobile, script_filled, resolved_lang)
    base_record["status"] = "initiated"
    base_record["twilio_call_sid"] = twilio_call_sid
    base_record["outcome"] = None
    base_record["transcript"] = None
    base_record["IMPORTANT"] = (
        "CALL IS NOW LIVE. Do NOT report any outcome, transcript, SIP confirmation, "
        "KYC actions, CRM logs, or next steps — the call has just started and none of "
        "that information exists yet. Report only the call_id and tell the RM the "
        "transcript will be available in GCS once the call ends."
    )
    _calls[call_id] = base_record
    return base_record


@mcp.tool()
def get_call_status(call_id: str) -> dict:
    """Get current status and transcript of a voice call."""
    if call_id not in _calls:
        return {"error": f"Call {call_id} not found"}
    return _calls[call_id]


@mcp.tool()
def get_recent_calls(rm_id: str, limit: int = 10) -> dict:
    """Get recent voice calls initiated by this RM."""
    rm_calls = [
        {k: v for k, v in c.items() if k != "transcript"}
        for c in _calls.values()
        if c.get("rm_id") == rm_id
    ]
    rm_calls.sort(key=lambda x: x.get("initiated_at", ""), reverse=True)
    return {"rm_id": rm_id, "calls": rm_calls[:limit]}


@mcp.tool()
def simulate_call_outcome(
    call_id: str,
    outcome: str,
    transcript: str,
) -> dict:
    """
    Record the outcome of a simulated or completed call.
    Used in PoC to demonstrate post-call CRM update flow.

    outcome: renewed | scheduled | callback_requested | not_interested | no_answer
    """
    if call_id not in _calls:
        return {"error": f"Call {call_id} not found"}

    _calls[call_id].update(
        {
            "status": "completed",
            "outcome": outcome,
            "transcript": transcript,
            "completed_at": datetime.utcnow().isoformat(),
        }
    )
    return {
        "call_id": call_id,
        "status": "completed",
        "outcome": outcome,
        "message": (
            f"Call outcome recorded: {outcome}. "
            "In production, this triggers: CRM interaction log update, "
            "follow-up draft creation, and next-action alert to RM."
        ),
    }


# ── WhatsApp Voice Note helpers ───────────────────────────────────────────────

def _normalize_mobile(mobile: str) -> str:
    """Ensure mobile has a leading + and country code."""
    mobile = mobile.strip().replace(" ", "").replace("-", "")
    if not mobile.startswith("+"):
        # Assume India (+91) if 10 digits
        mobile = f"+91{mobile}" if len(mobile) == 10 else f"+{mobile}"
    return mobile


def _build_tts_prompt(message_text: str, language: str) -> str:
    """Build a Director's Notes prompt for Gemini TTS to control accent and style."""
    if language.startswith("en"):
        accent = "Accent: Indian English — natural urban Indian cadence, clear and professional."
    elif language.startswith("hi"):
        accent = "Language: Hindi. Speak naturally in conversational Hindi."
    else:
        lang_name = SUPPORTED_LANGUAGES.get(language, {}).get("name", "the local language")
        accent = f"Language: {lang_name}. Speak naturally."
    return (
        "### DIRECTOR'S NOTES\n"
        "Style: Professional and warm — a trusted banking assistant, not a call centre robot.\n"
        "Pacing: Measured and clear. Pause naturally between sentences. Never rush amounts or dates.\n"
        f"{accent}\n\n"
        f"#### TRANSCRIPT\n"
        f"[warmly] {message_text}"
    )


def _tts_to_ogg(text: str, language_code: str) -> bytes:
    """Synthesize text to OGG Opus bytes using Gemini 3.1 Flash TTS (Autonoe voice)."""
    from google import genai
    from google.genai import types
    from pydub import AudioSegment

    client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
    prompt = _build_tts_prompt(text, language_code)
    response = client.models.generate_content(
        model="gemini-3.1-flash-tts-preview",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Autonoe"
                    )
                )
            ),
        ),
    )
    pcm_bytes = response.candidates[0].content.parts[0].inline_data.data
    # Gemini TTS outputs raw PCM: 24kHz, 16-bit, mono — convert to OGG/Opus for WhatsApp
    audio = AudioSegment.from_raw(
        io.BytesIO(pcm_bytes), sample_width=2, frame_rate=24000, channels=1
    )
    ogg_buf = io.BytesIO()
    audio.export(ogg_buf, format="ogg", codec="libopus")
    return ogg_buf.getvalue()


def _upload_audio_to_gcs(audio_bytes: bytes, filename: str) -> str:
    """Upload OGG audio to GCS and return the public URL."""
    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(GCS_BUCKET)
    blob = bucket.blob(f"voice-notes/{filename}")
    blob.upload_from_string(audio_bytes, content_type="audio/ogg")
    # Bucket uses uniform IAM (allUsers objectViewer) — no per-object ACL needed
    return blob.public_url


def _send_twilio_whatsapp_media(to_number: str, media_url: str, caption: str = "") -> str:
    """Post a WhatsApp media message via Twilio. Returns Twilio message SID."""
    resp = httpx.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json",
        auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
        data={
            "From": TWILIO_WHATSAPP_FROM,
            "To": f"whatsapp:{to_number}",
            "MediaUrl": media_url,
            "Body": caption,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("sid", "")


# Short voice note scripts (distinct from the interactive call scripts above)
_VOICE_NOTE_TEMPLATES: dict[str, str] = {
    "sip_renewal": (
        "{greeting} {client_name} ji! Cymbal Bank ki taraf se. "
        "Aapka {fund_name} SIP {expiry_date} expire ho raha hai. "
        "Renewal ke liye please humse sampark karein. {closing}!"
    ),
    "meeting_schedule": (
        "{greeting} {client_name}! This is Cymbal Bank. "
        "{rm_name} would like to schedule your portfolio review on {proposed_date}. "
        "Please let us know if the time works for you. {closing}!"
    ),
    "kyc_reminder": (
        "{greeting} {client_name} ji! Cymbal Bank reminder — "
        "aapka KYC {expiry_date} ko expire ho raha hai. "
        "Updated Aadhaar aur PAN submit karein. {closing}!"
    ),
    "birthday_greeting": (
        "{greeting} {client_name} ji! "
        "Cymbal Bank ki taraf se aapko bahut bahut badhaiyaan — Happy Birthday! "
        "Aapka vishwaas hamari shakti hai. {closing}!"
    ),
}


@mcp.tool()
def send_whatsapp_voice_note(
    mobile: str,
    client_name: str,
    note_id: str = "",
    message_text: str = "",
    language: str = "hi-IN",
    client_id: str = "",
    rm_id: str = "RM001",
    note_type: str = "custom",
) -> dict:
    """
    Generate a voice note audio from an approved script and deliver via WhatsApp.

    Preferred flow — always pass note_id from build_voice_note_script():
      The server fetches the exact stored script. Agent cannot alter the text.

    Fallback — pass message_text directly (only for custom/ad-hoc notes):
      Used when no build_voice_note_script() call was made.

    mobile: with country code e.g. "+919876543210"
    note_id: returned by build_voice_note_script() — use this to guarantee exact script
    message_text: only used if note_id is not provided
    language: BCP-47 code e.g. "hi-IN", "ta-IN", "en-IN"
    note_type: label for audit log
    """
    # Resolve script — note_id takes priority to guarantee exact approved text.
    # If agent forgot the note_id, fall back to the most recent script built for this client.
    resolved_note_id = note_id
    if not resolved_note_id or resolved_note_id not in _pending_scripts:
        resolved_note_id = _latest_note_by_mobile.get(client_name.lower(), "")

    if resolved_note_id and resolved_note_id in _pending_scripts:
        stored = _pending_scripts.pop(resolved_note_id)
        _latest_note_by_mobile.pop(client_name.lower(), None)
        message_text = stored["message_text"]
        language = stored.get("language", language)
        note_type = stored.get("note_type", note_type)
        if not client_name:
            client_name = stored.get("client_name", client_name)
        log.info("Using stored script note_id=%s for client=%s: %r", resolved_note_id, client_name, message_text)
    elif not message_text:
        return {"error": "No stored script found. Call build_voice_note_script() first."}

    resolved_lang = _resolve_language(language)
    lang_info = SUPPORTED_LANGUAGES[resolved_lang]
    normalized_mobile = _normalize_mobile(mobile)
    wa_note_id = f"WA-{uuid.uuid4().hex[:8].upper()}"
    audio_filename = f"{wa_note_id}.ogg"

    base_record = {
        "note_id": wa_note_id,
        "script_note_id": note_id or "custom",
        "client_id": client_id,
        "rm_id": rm_id,
        "mobile": normalized_mobile,
        "language": resolved_lang,
        "language_name": lang_info["name"],
        "note_type": note_type,
        "message_preview": message_text[:150],
        "initiated_at": datetime.utcnow().isoformat(),
    }

    if not TWILIO_ACCOUNT_SID:
        base_record["status"] = "simulated"
        base_record["note"] = (
            f"PoC mode — {lang_info['name']} voice note to {client_name} staged. "
            "TTS engine: gemini-3.1-flash-tts-preview. "
            "Set TWILIO_ACCOUNT_SID + TWILIO_AUTH_TOKEN + GCS_BUCKET to send real notes."
        )
        base_record["audio_url"] = f"gs://{GCS_BUCKET}/voice-notes/{audio_filename} (not generated)"
        return base_record

    try:
        audio_bytes = _tts_to_ogg(message_text, resolved_lang)
    except Exception as exc:
        return {**base_record, "status": "error", "error": f"TTS failed: {exc}"}

    try:
        public_url = _upload_audio_to_gcs(audio_bytes, audio_filename)
    except Exception as exc:
        return {**base_record, "status": "error", "error": f"GCS upload failed: {exc}"}

    try:
        twilio_sid = _send_twilio_whatsapp_media(
            to_number=normalized_mobile,
            media_url=public_url,
            caption=f"Voice note from your Cymbal Bank Relationship Manager ({lang_info['name']})",
        )
    except Exception as exc:
        return {
            **base_record,
            "status": "error",
            "error": f"Twilio send failed: {exc}",
            "audio_url": public_url,
        }

    base_record.update({
        "status": "sent",
        "twilio_message_sid": twilio_sid,
        "audio_url": public_url,
        "listen_url": public_url,
        "message": (
            f"Voice note sent to {client_name} on WhatsApp ({lang_info['name']}). "
            f"Twilio SID: {twilio_sid}. "
            f"Listen to sent audio: {public_url}"
        ),
    })
    return base_record


@mcp.tool()
def build_voice_note_script(
    note_type: str,
    client_name: str,
    language: str = "hi-IN",
    script_variables: dict | None = None,
) -> dict:
    """
    Generate the text for a WhatsApp voice note from a template.
    Use the returned 'message_text' as input to send_whatsapp_voice_note().

    note_type: sip_renewal | meeting_schedule | kyc_reminder | birthday_greeting
    script_variables: dict of values to fill the template, e.g.:
      {"fund_name": "HDFC Mid-Cap", "monthly_amount": "25,000", "expiry_date": "28 May 2026"}
    """
    if note_type not in _VOICE_NOTE_TEMPLATES:
        return {
            "error": f"Unknown note_type '{note_type}'. Valid: {list(_VOICE_NOTE_TEMPLATES.keys())}"
        }

    resolved_lang = _resolve_language(language)
    lang_info = SUPPORTED_LANGUAGES[resolved_lang]
    all_vars = {
        "client_name": client_name,
        "greeting": lang_info["greeting"],
        "closing": lang_info["closing"],
        **(script_variables or {}),
    }

    try:
        text = _VOICE_NOTE_TEMPLATES[note_type].format(**all_vars)
    except KeyError as exc:
        return {
            "error": f"Missing script variable: {exc}",
            "required_variables": [
                k for k in _VOICE_NOTE_TEMPLATES[note_type].split("{")
                if "}" in k
                if k.split("}")[0] not in ("greeting", "closing", "client_name")
            ],
        }

    note_id = f"NOTE-{uuid.uuid4().hex[:8].upper()}"
    # Only store the FIRST build per client in _pending_scripts.
    # Subsequent builds (Memory Bank contamination) are discarded entirely —
    # not stored anywhere — so the agent cannot look them up or use them.
    if client_name.lower() not in _latest_note_by_mobile:
        _pending_scripts[note_id] = {
            "message_text": text,
            "note_type": note_type,
            "language": resolved_lang,
            "client_name": client_name,
        }
        _latest_note_by_mobile[client_name.lower()] = note_id
        log.info("Script stored (first): note_id=%s type=%s lang=%s text=%r", note_id, note_type, resolved_lang, text)
    else:
        locked = _latest_note_by_mobile[client_name.lower()]
        log.info("Script discarded (not stored): note_id=%s — locked build %s already set for client=%s",
                 note_id, locked, client_name)
    return {
        "note_id": note_id,
        "note_type": note_type,
        "language": resolved_lang,
        "language_name": lang_info["name"],
        "message_text": text,
        "char_count": len(text),
        "instruction": (
            f"Show this script to the RM for approval. "
            f"When approved, pass note_id='{note_id}' to send_whatsapp_voice_note(). "
            f"Do NOT modify or rephrase the message_text — the server will use the stored script."
        ),
    }


# ── Interactive voice call helpers ────────────────────────────────────────────

def _prepare_bridge(
    call_id: str,
    language: str,
    client_name: str,
    rm_name: str,
    rm_message: str,
    script_variables: dict,
) -> None:
    """Push call context to the LiveAPI Broker before Twilio dials."""
    payload = {
        "language": language,
        "client_name": client_name,
        "rm_name": rm_name,
        "rm_message": rm_message,
        "system_instruction": rm_message,
        "products": script_variables.get("products", []),
    }
    log.info(
        "Preparing bridge: call=%s  language=%s  client=%s  rm=%s",
        call_id, language, client_name, rm_name,
    )
    try:
        resp = httpx.post(
            f"{LIVEAPI_BROKER_URL}/calls/{call_id}/prepare",
            json=payload,
            timeout=5,
        )
        resp.raise_for_status()
        log.info("Bridge prepared OK: call=%s", call_id)
    except httpx.HTTPStatusError as exc:
        log.error(
            "Bridge /prepare failed (HTTP %s) for call=%s: %s",
            exc.response.status_code, call_id, exc.response.text,
        )
        raise
    except Exception as exc:
        log.error("Bridge /prepare request failed for call=%s: %s", call_id, exc)
        raise


def _place_twilio_call(call_id: str, mobile: str, script: str, language: str) -> str:
    """Place real Twilio call that connects to the LiveAPI Broker."""
    webhook_url = f"{LIVEAPI_BROKER_URL}/twilio/voice/{call_id}"
    status_url = f"{LIVEAPI_BROKER_URL}/twilio/status/{call_id}"
    response = httpx.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Calls.json",
        auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
        data={
            "To": mobile,
            "From": TWILIO_FROM_NUMBER,
            "Url": webhook_url,
            "Method": "POST",
            "StatusCallback": status_url,
            "StatusCallbackMethod": "POST",
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json().get("sid", "")




if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8005))
    inner = mcp.streamable_http_app()

    uvicorn.run(inner, host="0.0.0.0", port=port)
