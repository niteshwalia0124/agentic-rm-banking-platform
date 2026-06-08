"""
LiveAPI Broker — Twilio Media Streams ↔ Gemini Live API, with transcript storage.

Two responsibilities:
  1. Bridge live call audio in both directions:
       Twilio Media Stream (μ-law 8kHz) ⇄ Gemini Live API (PCM 16kHz)
  2. Store speaker-labelled transcript per call for the RM dashboard.

Run locally:
  uvicorn bridge.liveapi_broker:app --port 8010 --reload
"""

import asyncio
try:
    import audioop  # Python ≤3.12
except ImportError:
    import audioop_lts as audioop  # Python 3.13+ (audioop removed from stdlib)
import base64
import json
import logging
import os
import time

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from twilio.request_validator import RequestValidator

from google import genai
from google.genai import types as genai_types

log = logging.getLogger("liveapi_broker")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Agent Teams for Relationship Managers — LiveAPI Broker")

TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
_twilio_validator = RequestValidator(TWILIO_AUTH_TOKEN) if TWILIO_AUTH_TOKEN else None


def _validate_twilio(request: Request, form: dict) -> None:
    """Reject requests that don't carry a valid Twilio signature.

    Cloud Run terminates TLS so request.url.scheme is 'http' inside the
    container. Reconstruct the public HTTPS URL Twilio actually signed.
    """
    if not _twilio_validator:
        return
    scheme = request.headers.get("x-forwarded-proto", "https")
    host = request.headers.get("host", request.url.netloc)
    url = f"{scheme}://{host}{request.url.path}"
    if request.url.query:
        url += f"?{request.url.query}"
    sig = request.headers.get("X-Twilio-Signature", "")
    if not _twilio_validator.validate(url, form, sig):
        log.warning("Twilio signature mismatch for %s — rejecting", url)
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")


GEMINI_LIVE_MODEL = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live-preview")

GCP_PROJECT = os.getenv("GCP_PROJECT", "<YOUR_GCP_PROJECT>")
GCP_LOCATION = os.getenv("GCP_LOCATION", "global")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

TRANSCRIPT_DIR = os.getenv("TRANSCRIPT_DIR", "transcripts")
GCS_BUCKET = os.getenv("GCS_BUCKET", "fsi-rm-voice-notes")          # voice notes
TRANSCRIPT_BUCKET = os.getenv("TRANSCRIPT_BUCKET", "fsi-rm-call-transcripts")  # call transcripts

# Per-call state set by voice-mcp before the call is placed
CALL_CONTEXT: dict[str, dict] = {}

# In-memory transcript store: call_id → list of {speaker, text, ts}
# Final segments only; flushed to disk on call end.
TRANSCRIPTS: dict[str, list[dict]] = {}

# Use Google AI Developer API (API key) if GOOGLE_API_KEY is set —
# gemini-3.1-flash-live-preview requires Vertex AI Live API allowlist approval
# per project; AI Studio key bypasses this for the PoC.
if GOOGLE_API_KEY:
    genai_client = genai.Client(api_key=GOOGLE_API_KEY)
else:
    genai_client = genai.Client(
        vertexai=True,
        project=GCP_PROJECT,
        location=GCP_LOCATION,
    )

# Gemini Live does not respond to WebSocket protocol-level ping frames.
# Without this, the websockets library closes the connection with 1011 after
# ~20 s of Gemini silence (e.g. while waiting for the user to speak).
genai_client._api_client._websocket_ssl_ctx["ping_interval"] = None


@app.get("/health")
def health():
    return {"status": "ok", "active_calls": len(CALL_CONTEXT)}


@app.post("/calls/{call_id}/prepare")
async def prepare_call(call_id: str, ctx: dict):
    """Voice MCP calls this just before dialling Twilio — sets the language,
    client profile, and system instruction for the Gemini Live session."""
    CALL_CONTEXT[call_id] = ctx
    return {"call_id": call_id, "ready": True}


@app.get("/calls/{call_id}/transcript")
def get_transcript(call_id: str):
    """Return the stored transcript for a completed or in-progress call."""
    # Check in-memory first (call still active), then fall back to file
    if call_id in TRANSCRIPTS:
        return TRANSCRIPTS[call_id]
    path = os.path.join(TRANSCRIPT_DIR, f"{call_id}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    raise HTTPException(status_code=404, detail="Transcript not found")


@app.get("/transcripts")
def list_transcripts():
    """List all call IDs with saved transcripts."""
    active = list(TRANSCRIPTS.keys())
    saved = []
    if os.path.isdir(TRANSCRIPT_DIR):
        saved = [f.replace(".json", "") for f in os.listdir(TRANSCRIPT_DIR) if f.endswith(".json")]
    return {"active": active, "saved": saved}


@app.api_route("/twilio/voice/{call_id}", methods=["GET", "POST"])
async def twilio_voice_webhook(call_id: str, request: Request):
    """
    Twilio hits this when the client picks up. We return TwiML that opens a
    bidirectional Media Stream WebSocket back to /twilio/{call_id}.
    """
    if request.method == "POST":
        form = dict(await request.form())
        _validate_twilio(request, form)

    host = request.headers.get("host", "localhost:8010")
    fwd_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    proto = "wss" if fwd_proto == "https" else "ws"
    stream_url = f"{proto}://{host}/twilio/{call_id}"

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{stream_url}" />
  </Connect>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


@app.post("/twilio/status/{call_id}")
async def twilio_status_callback(call_id: str, request: Request):
    """Twilio call status updates — log them, optionally forward to coach server."""
    form = dict(await request.form())
    _validate_twilio(request, form)
    status = form.get("CallStatus", "unknown")
    log.info("Twilio status for %s: %s", call_id, status)
    return JSONResponse({"ack": True})


@app.websocket("/twilio/{call_id}")
async def twilio_media_stream(twilio_ws: WebSocket, call_id: str):
    """
    Twilio opens this WebSocket once the call connects.
    Audio frames flow bidirectionally as JSON {event, media: {payload: base64}}.
    """
    await twilio_ws.accept()
    ctx = CALL_CONTEXT.get(call_id, {})
    if not ctx:
        log.warning(
            "No context for call=%s — /prepare was not called or failed. "
            "Defaulting to hi-IN / no client name. Voice agent will use 'aap'.",
            call_id,
        )
    language = ctx.get("language", "hi-IN")
    log.info(
        "Twilio stream connected: call=%s  lang=%s  client=%s  rm=%s",
        call_id, language, ctx.get("client_name", "<unknown>"), ctx.get("rm_name", "<unknown>"),
    )

    config = genai_types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=ctx.get("system_instruction", _default_system_instruction(language, ctx)),
        speech_config=genai_types.SpeechConfig(
            language_code=language,
            voice_config=genai_types.VoiceConfig(
                prebuilt_voice_config=genai_types.PrebuiltVoiceConfig(
                    voice_name="Aoede",
                )
            ),
        ),
        input_audio_transcription=genai_types.AudioTranscriptionConfig(),
        output_audio_transcription=genai_types.AudioTranscriptionConfig(),
        # Telephony audio (8kHz μ-law upsampled to 16kHz) has limited bandwidth.
        # START_SENSITIVITY_HIGH fires VAD on narrowband speech.
        # silence_duration_ms=800 keeps response latency under ~1s after user stops speaking.
        realtime_input_config=genai_types.RealtimeInputConfig(
            automatic_activity_detection=genai_types.AutomaticActivityDetection(
                start_of_speech_sensitivity=genai_types.StartSensitivity.START_SENSITIVITY_HIGH,
                end_of_speech_sensitivity=genai_types.EndSensitivity.END_SENSITIVITY_HIGH,
                prefix_padding_ms=200,
                silence_duration_ms=600,
            ),
        ),
    )

    async with genai_client.aio.live.connect(model=GEMINI_LIVE_MODEL, config=config) as live:
        # Trigger the opening greeting immediately without waiting for user audio.
        await live.send_client_content(
            turns=genai_types.Content(
                role="user",
                parts=[genai_types.Part(text="(Call connected. Please begin your opening greeting now.)")],
            ),
            turn_complete=True,
        )
        log.info("Sent initial turn to Gemini Live for call=%s", call_id)

        # _gemini_to_twilio sets this once the greeting turn_complete fires.
        # _twilio_to_gemini discards all audio until then to prevent barge-in
        # on ambient phone noise cutting the greeting short.
        greeting_done = asyncio.Event()
        stream_sid_future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        TRANSCRIPTS[call_id] = []
        try:
            await asyncio.gather(
                _twilio_to_gemini(twilio_ws, live, stream_sid_future, greeting_done),
                _gemini_to_twilio(live, twilio_ws, call_id, stream_sid_future, greeting_done),
            )
        finally:
            _save_transcript(call_id)
            CALL_CONTEXT.pop(call_id, None)
            log.info("Call ended: %s", call_id)


async def _twilio_to_gemini(twilio_ws: WebSocket, live, stream_sid_future: asyncio.Future,
                            greeting_done: asyncio.Event):
    """Forward Twilio caller audio → Gemini Live input."""
    ratecv_state = None
    frames_sent = 0
    try:
        while True:
            msg = await twilio_ws.receive_text()
            data = json.loads(msg)

            if data.get("event") == "start":
                sid = data["start"]["streamSid"]
                if not stream_sid_future.done():
                    stream_sid_future.set_result(sid)
                log.info("Twilio stream started: streamSid=%s", sid)

            elif data.get("event") == "media":
                # Only inbound (caller→bridge); outbound is Gemini's own audio echoed back.
                if data["media"].get("track") == "outbound":
                    continue

                # Drop audio while Gemini is delivering the opening greeting.
                if not greeting_done.is_set():
                    continue

                # μ-law 8kHz → PCM 16-bit 16kHz (Gemini Live requirement)
                mu_law = base64.b64decode(data["media"]["payload"])
                pcm_8k = audioop.ulaw2lin(mu_law, 2)
                pcm_16k, ratecv_state = audioop.ratecv(pcm_8k, 2, 1, 8000, 16000, ratecv_state)

                await live.send_realtime_input(
                    audio=genai_types.Blob(data=pcm_16k, mime_type="audio/pcm;rate=16000")
                )
                frames_sent += 1
                if frames_sent == 1:
                    log.info("First audio frame sent — RMS=%d", audioop.rms(pcm_16k, 2))
                elif frames_sent % 100 == 0:
                    log.info("Audio frames: %d  RMS=%d", frames_sent, audioop.rms(pcm_16k, 2))

            elif data.get("event") == "stop":
                log.info("Twilio stream stopped (total frames: %d)", frames_sent)
                break

    except WebSocketDisconnect:
        log.info("Twilio WS disconnected")


async def _gemini_to_twilio(live, twilio_ws: WebSocket, call_id: str,
                            stream_sid_future: asyncio.Future, greeting_done: asyncio.Event):
    """Forward Gemini Live audio → Twilio; handle barge-in; store transcripts."""
    stream_sid = None
    ratecv_state = None
    first_turn_done = False
    _last_user_text = ""
    _last_gemini_text = ""
    try:
        while True:
            turn = live.receive()
            async for response in turn:
                # ── Audio ──────────────────────────────────────────────────────
                if response.data:
                    # Gemini Live outputs PCM at 24kHz — downsample to 8kHz μ-law for Twilio.
                    # Using 16000 here caused 2/3-speed playback and a dull, low-pitched voice.
                    pcm_24k = response.data
                    pcm_8k, ratecv_state = audioop.ratecv(pcm_24k, 2, 1, 24000, 8000, ratecv_state)
                    mu_law = audioop.lin2ulaw(pcm_8k, 2)
                    payload = base64.b64encode(mu_law).decode("ascii")

                    if stream_sid is None:
                        stream_sid = await stream_sid_future

                    await twilio_ws.send_text(json.dumps({
                        "event": "media",
                        "streamSid": stream_sid,
                        "media": {"payload": payload},
                    }))

                # ── Server content (transcripts, turn signals, interruptions) ──
                if response.server_content:
                    sc = response.server_content

                    # Barge-in: user spoke while Gemini was talking.
                    # Gemini stops generating; we must flush Twilio's audio buffer
                    # so the caller doesn't hear the tail of the interrupted response.
                    if sc.interrupted and stream_sid:
                        await twilio_ws.send_text(json.dumps({
                            "event": "clear",
                            "streamSid": stream_sid,
                        }))
                        log.info("Barge-in detected — Twilio buffer cleared for call=%s", call_id)

                    # Release the audio gate once Gemini finishes the opening greeting.
                    if not first_turn_done and sc.turn_complete:
                        first_turn_done = True
                        greeting_done.set()
                        log.info("Greeting complete for call=%s — user audio now active", call_id)

                    if sc.input_transcription and sc.input_transcription.text:
                        _last_user_text += sc.input_transcription.text
                        log.info("USER [%s]: %s", call_id, sc.input_transcription.text)

                    if sc.output_transcription and sc.output_transcription.text:
                        _last_gemini_text += sc.output_transcription.text
                        log.info("GEMINI [%s]: %s", call_id, sc.output_transcription.text)

                    # Flush completed turns to transcript on turn_complete.
                    # finished=True on individual transcription chunks is unreliable
                    # in Gemini Live streaming — turn_complete is the reliable signal.
                    if sc.turn_complete:
                        ts = time.time()
                        if _last_user_text and call_id in TRANSCRIPTS:
                            TRANSCRIPTS[call_id].append(
                                {"speaker": "client", "text": _last_user_text, "ts": ts}
                            )
                            _last_user_text = ""
                        if _last_gemini_text and call_id in TRANSCRIPTS:
                            TRANSCRIPTS[call_id].append(
                                {"speaker": "rm", "text": _last_gemini_text, "ts": ts}
                            )
                            _last_gemini_text = ""

    except Exception as e:
        log.error("Gemini Live error on call %s: %s", call_id, e)
    finally:
        # Flush any text that was accumulated but never flushed via turn_complete
        # (happens when Twilio hangs up before Gemini fires the final turn_complete)
        if (_last_user_text or _last_gemini_text) and call_id in TRANSCRIPTS:
            ts = time.time()
            if _last_user_text:
                TRANSCRIPTS[call_id].append(
                    {"speaker": "client", "text": _last_user_text.strip(), "ts": ts}
                )
            if _last_gemini_text:
                TRANSCRIPTS[call_id].append(
                    {"speaker": "priya", "text": _last_gemini_text.strip(), "ts": ts}
                )
            log.info("Flushed %d pending segment(s) for call=%s",
                     int(bool(_last_user_text)) + int(bool(_last_gemini_text)), call_id)


def _save_transcript(call_id: str) -> None:
    """Write the in-memory transcript to a local file and GCS on call end."""
    segments = TRANSCRIPTS.pop(call_id, [])
    log.info("Saving transcript for call=%s — %d segments", call_id, len(segments))
    if not segments:
        log.warning("No transcript segments for call=%s — nothing to save", call_id)
        return
    payload = json.dumps(segments, ensure_ascii=False, indent=2)

    # Save locally (ephemeral fallback)
    os.makedirs(TRANSCRIPT_DIR, exist_ok=True)
    path = os.path.join(TRANSCRIPT_DIR, f"{call_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write(payload)
    log.info("Transcript saved locally: %s (%d segments)", path, len(segments))

    # Save to dedicated transcript bucket for compliance
    try:
        from google.cloud import storage as gcs
        gcs_client = gcs.Client()
        bucket = gcs_client.bucket(TRANSCRIPT_BUCKET)
        blob = bucket.blob(f"{call_id}.json")
        blob.upload_from_string(payload, content_type="application/json")
        log.info("Transcript uploaded to GCS: gs://%s/%s.json (%d segments)",
                 TRANSCRIPT_BUCKET, call_id, len(segments))
    except Exception as exc:
        log.error("GCS transcript upload failed: %s", exc)
        raise  # re-raise so the error is visible in Cloud Logging


def _default_system_instruction(language: str, ctx: dict = None) -> str:
    ctx = ctx or {}
    rm_name = ctx.get("rm_name", "Ravi Gupta")
    client_name = ctx.get("client_name", "")
    rm_message = ctx.get("rm_message", "")
    products = ctx.get("products", [])

    client_ref = f"{client_name} ji" if client_name else "aap"
    product_lines = ""
    if products:
        product_lines = (
            "Products to present today (mention only if relevant, with disclaimer):\n"
            + "\n".join(f"  - {p}" for p in products) + "\n"
        )

    message_line = ""
    if rm_message:
        message_line = f"\nImportant message from {rm_name} to share with the client:\n  {rm_message}\n"

    return f"""You are Priya, a warm and professional AI voice assistant calling on behalf of {rm_name}, Relationship Manager at Cymbal Bank, who manages {client_ref}'s portfolio.

INTRODUCTION (always follow this sequence):
1. Greet: "Namaste, kya main {client_name + ' ji' if client_name else '[client name] ji'} se baat kar sakti hoon?"
2. Wait for the client to confirm their identity. Do not proceed until they say yes or confirm their name.
3. Introduce yourself: "Main Priya hoon, {rm_name} ji ki AI assistant. {rm_name} ji ne mujhe aapko call karne ke liye kaha hai."
4. State the purpose briefly and get into it — do NOT offer to call back if the client is already engaged.
{message_line}
YOUR ROLE:
- You are {rm_name}'s helper. You act on tasks he assigns: sharing updates, presenting products, scheduling, noting client feedback.
- Be warm, polite, and respectful at all times. Address the client formally (ji, aap).
- Keep each response to ONE point. Say it, then pause and listen. Never pile up questions.
- If the client asks something you are not briefed on, say: "Yeh main {rm_name} ji se confirm karungi aur woh aapko detail mein batayenge."
{product_lines}
WHAT YOU DO:
- Share portfolio updates or information that {rm_name} has asked you to convey.
- Present new bank products (savings, FD, loans) and insurance products — warmly, without pressure.
- Schedule a callback or branch visit with {rm_name} if the client requests it.
- Note any message, query, or feedback the client wants to pass to {rm_name}, and acknowledge: "Main yeh zaroor {rm_name} ji tak pahunchaungi."

WHAT YOU NEVER DO:
- Never collect financial details, account numbers, PINs, passwords, OTPs, or any sensitive data from the client.
- Never make specific investment recommendations beyond what {rm_name} has briefed you to share.
- Never say "I will update Ravi" — always use {rm_name}'s correct name.

COMPLIANCE:
- When mentioning any market-linked product, always add: "yeh market risks ke adheen hai."
- Be transparent that you are an AI assistant, if asked directly.
- Only call between 8am–7pm local time (enforced externally, but keep calls brief).

CLOSING (always end every call with this, word for word):
"Main {rm_name} ji ko hamare is baatcheet ke baare mein update karungi. Koi bhi sawaal ke liye aap seedha {rm_name} ji se sampark kar sakte hain. Bahut bahut shukriya, {client_ref}. Namaste!"
"""


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8010)))
