"""
Voice Coach Server — real-time coaching hint coordinator.

Sits between the Pipecat bridge (which produces live transcripts) and the RM's
browser dashboard (which displays sentiment + coaching hints during the call).

Endpoints:
  GET   /                         → RM dashboard HTML (open during a call)
  GET   /healthz                  → health check
  POST  /calls/{call_id}/register → voice-mcp registers a new call (metadata + language)
  WS    /transcript/{call_id}     → Pipecat bridge streams transcript chunks here
  WS    /coach/{call_id}          → RM browser subscribes to hint stream here
  GET   /calls/{call_id}/summary  → post-call summary (sentiment timeline, missed objections)

Hint generation:
  Every new client turn OR every 5 seconds (whichever first) → run coach agent on
  rolling 8-turn buffer → broadcast structured JSON hint to all RM subscribers.
"""

import asyncio
import json
import logging
import os
import time
from collections import deque
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

from agents.voice_coach.agent import voice_coach_agent

log = logging.getLogger("coach_server")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Agent Teams for Relationship Managers — Voice Coach")

COACH_APP = "fsi-rm-voice-coach"
HINT_INTERVAL_SECONDS = float(os.getenv("COACH_HINT_INTERVAL", "5"))
TRANSCRIPT_WINDOW_TURNS = 8

# ── Coach agent runner ────────────────────────────────────────────────────────
session_service = InMemorySessionService()  # ephemeral — coach state lives only during call
runner = Runner(
    agent=voice_coach_agent,
    app_name=COACH_APP,
    session_service=session_service,
)


# ── Per-call state ────────────────────────────────────────────────────────────
class CallState:
    def __init__(self, call_id: str, metadata: dict):
        self.call_id = call_id
        self.metadata = metadata  # {rm_id, client_id, client_name, language, purpose}
        self.transcript: deque[dict] = deque(maxlen=200)  # full transcript history
        self.rm_subscribers: list[WebSocket] = []
        self.hint_history: list[dict] = []
        self.last_hint_at: float = 0.0
        self.coach_session_id = f"coach-{call_id}"


CALLS: dict[str, CallState] = {}


# ── Dashboard HTML ────────────────────────────────────────────────────────────

DASHBOARD_PATH = Path(__file__).parent / "dashboard.html"


@app.get("/", response_class=HTMLResponse)
def dashboard_index():
    return DASHBOARD_PATH.read_text()


@app.get("/healthz")
def health():
    return {"status": "ok", "active_calls": len(CALLS)}


# ── Voice MCP registers a new call here when initiate_voice_call() fires ──────

@app.post("/calls/{call_id}/register")
async def register_call(call_id: str, request: Request):
    meta = await request.json()
    CALLS[call_id] = CallState(call_id, meta)
    await session_service.create_session(
        app_name=COACH_APP,
        user_id=meta.get("rm_id", "rm-default"),
        session_id=f"coach-{call_id}",
    )
    dashboard_url = f"{os.getenv('COACH_SERVER_URL', 'http://localhost:8006')}/?call_id={call_id}"
    log.info("Call registered: %s  language=%s  dashboard=%s",
             call_id, meta.get("language"), dashboard_url)
    return JSONResponse({"call_id": call_id, "dashboard_url": dashboard_url})


# ── Pipecat bridge → transcript ingress ───────────────────────────────────────

@app.websocket("/transcript/{call_id}")
async def transcript_ingress(ws: WebSocket, call_id: str):
    """
    Pipecat bridge streams transcript chunks here. Format per chunk:
        {"speaker": "client" | "rm", "text": "...", "ts": 1234567890.1, "is_final": true}
    """
    await ws.accept()
    state = CALLS.get(call_id)
    if state is None:
        state = CallState(call_id, {})
        CALLS[call_id] = state

    try:
        while True:
            chunk = await ws.receive_json()
            state.transcript.append(chunk)

            # Echo transcript to all RM subscribers immediately (so they see live convo)
            await _broadcast(state, {"type": "transcript", "chunk": chunk})

            should_coach = (
                chunk.get("speaker") == "client" and chunk.get("is_final")
                or (time.time() - state.last_hint_at) > HINT_INTERVAL_SECONDS
            )
            if should_coach and state.transcript:
                asyncio.create_task(_generate_hint(state))
    except WebSocketDisconnect:
        log.info("Transcript ingress closed for %s", call_id)


# ── RM browser → hint subscription ────────────────────────────────────────────

@app.websocket("/coach/{call_id}")
async def coach_subscribe(ws: WebSocket, call_id: str):
    """RM browser subscribes here. Receives transcript echoes + hints in real time."""
    await ws.accept()
    state = CALLS.get(call_id)
    if state is None:
        # Allow subscription before call registration (RM opens dashboard early)
        state = CallState(call_id, {})
        CALLS[call_id] = state

    state.rm_subscribers.append(ws)

    # Send call metadata + replay any hints already generated
    await ws.send_json({"type": "init", "metadata": state.metadata,
                        "history": state.hint_history})

    try:
        while True:
            # Keepalive — RM page may ping
            await ws.receive_text()
    except WebSocketDisconnect:
        state.rm_subscribers.remove(ws)
        log.info("RM dashboard disconnected from %s", call_id)


# ── Post-call summary ─────────────────────────────────────────────────────────

@app.get("/calls/{call_id}/summary")
def call_summary(call_id: str):
    state = CALLS.get(call_id)
    if not state:
        return JSONResponse({"error": "unknown call_id"}, status_code=404)

    sentiments = [h.get("sentiment") for h in state.hint_history if h.get("sentiment")]
    objections = [h.get("objection_detected") for h in state.hint_history
                  if h.get("objection_detected")]
    flags = [h.get("compliance_flag") for h in state.hint_history if h.get("compliance_flag")]

    return JSONResponse({
        "call_id": call_id,
        "metadata": state.metadata,
        "transcript_turns": len(state.transcript),
        "hint_count": len(state.hint_history),
        "sentiment_timeline": sentiments,
        "objections_raised": list(dict.fromkeys(objections)),  # dedupe, keep order
        "compliance_flags": list(dict.fromkeys(flags)),
        "final_sentiment": sentiments[-1] if sentiments else None,
    })


# ── Hint generation ───────────────────────────────────────────────────────────

async def _generate_hint(state: CallState):
    """Run the coach agent on the recent transcript window, broadcast the JSON hint."""
    state.last_hint_at = time.time()

    window = list(state.transcript)[-TRANSCRIPT_WINDOW_TURNS:]
    transcript_block = "\n".join(
        f'[{c.get("speaker", "?")}] {c.get("text", "")}' for c in window
    )
    language = state.metadata.get("language", "en-IN")
    client_name = state.metadata.get("client_name", "the client")

    prompt = (
        f"Call language: {language}\n"
        f"Client: {client_name}\n"
        f"Recent transcript:\n{transcript_block}\n\n"
        "Emit the coaching JSON now."
    )

    msg = Content(role="user", parts=[Part(text=prompt)])
    rm_id = state.metadata.get("rm_id", "rm-default")
    response_text = ""

    try:
        async for event in runner.run_async(
            user_id=rm_id, session_id=state.coach_session_id, new_message=msg
        ):
            if event.is_final_response() and event.content:
                response_text = "".join(
                    p.text for p in event.content.parts if hasattr(p, "text")
                )
    except Exception as e:
        log.error("Coach agent error for %s: %s", state.call_id, e)
        return

    hint = _safe_parse_hint(response_text)
    if not hint:
        return

    hint["ts"] = time.time()
    state.hint_history.append(hint)
    await _broadcast(state, {"type": "hint", "hint": hint})


def _safe_parse_hint(text: str) -> dict[str, Any] | None:
    text = text.strip()
    # Strip ``` fences if the model added them despite instructions
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log.warning("Coach agent returned non-JSON: %s", text[:200])
        return None


async def _broadcast(state: CallState, payload: dict):
    """Send payload to all RM subscribers for this call. Drop dead sockets silently."""
    dead = []
    for ws in state.rm_subscribers:
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        state.rm_subscribers.remove(ws)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8006)))
