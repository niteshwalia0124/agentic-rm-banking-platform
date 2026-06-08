"""
Orchestrator A2A Gateway
Wraps the ADK orchestrator with an A2A-compatible HTTP surface so that
Gemini Enterprise (Agentspace) can discover and call it via the A2A Protocol.

How it fits in the architecture:
  RM in Gemini for Workspace
        ↓  A2A (Agentspace discovers via Agent Card)
  THIS SERVER (Cloud Run)
        ↓  ADK Runner.run()
  Orchestrator LlmAgent  (in-process — all 6 sub-agents via AgentTool)
        ↓  MCP
  5 MCP Servers  →  AWS Lambda A2A agents

A2A Protocol endpoints:
  GET  /.well-known/agent.json   → Agent Card (capabilities, skills)
  POST /                         → JSON-RPC tasks/send handler
  POST /stream                   → JSON-RPC tasks/sendSubscribe (SSE streaming)

Deploy:
  gcloud run deploy fsi-rm-a2a-gateway --source gateway/ --region asia-south1
  Then register the Cloud Run URL in Agentspace as an external A2A agent.

Run locally:
  uvicorn gateway.a2a_server:app --port 8080 --reload
"""

import json
import logging
import os
import uuid
from datetime import datetime
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from google.adk.memory.vertex_ai_memory_bank_service import VertexAiMemoryBankService
from google.adk.runners import Runner
from google.adk.sessions import VertexAiSessionService
from google.genai.types import Content, Part

# Import the orchestrator — all sub-agents are loaded in-process
from agents.orchestrator.agent import orchestrator

log = logging.getLogger("a2a_gateway")

app = FastAPI(title="Agent Teams for Relationship Managers — A2A Gateway")

from fastapi import Request
import time

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    log.info(
        f"Request: {request.method} {request.url.path} - Status: {response.status_code} - Content-Type: {response.headers.get('content-type')} - Time: {process_time:.4f}s"
    )
    return response

APP_NAME = "fsi-rm-agentspace"

# ── Vertex AI Memory Bank + Session Service ──────────────────────────────────
# Both require an Agent Engine ID — created once via `adk deploy` or
# `gcloud ai agent-engines create`. Set AGENT_ENGINE_ID in the environment.
#
# Memory Bank semantics:
#   - VertexAiSessionService: persists per-session conversation turns (durable across
#     Cloud Run restarts, replaces InMemorySessionService).
#   - VertexAiMemoryBankService: auto-extracts topical long-term memories from
#     completed sessions ("RM asked about Rahul's tax-saving" → "Rahul interested
#     in ELSS for FY26 tax savings"). PreloadMemoryTool on the orchestrator
#     retrieves these on every new turn.
GCP_PROJECT = os.environ["GCP_PROJECT"]
GCP_LOCATION = os.getenv("GCP_LOCATION", "us-east1")
AGENT_ENGINE_ID = os.environ["AGENT_ENGINE_ID"]

session_service = VertexAiSessionService(
    project=GCP_PROJECT,
    location=GCP_LOCATION,
    agent_engine_id=AGENT_ENGINE_ID,
)

memory_service = VertexAiMemoryBankService(
    project=GCP_PROJECT,
    location=GCP_LOCATION,
    agent_engine_id=AGENT_ENGINE_ID,
)

runner = Runner(
    agent=orchestrator,
    app_name=APP_NAME,
    session_service=session_service,
    memory_service=memory_service,
)

# ── Agent Card ────────────────────────────────────────────────────────────────
# Gemini Enterprise Agentspace fetches this to discover capabilities.
# Register this server's Cloud Run URL in Agentspace → it reads this card.
GATEWAY_URL = os.getenv("GATEWAY_URL", "https://fsi-rm-a2a-gateway.run.app")

AGENT_CARD = {
    "name": "FSI Relationship Manager AI",
    "version": "1.0",
    "protocolVersion": "0.3.0",
    "description": (
        "AI assistant for bank Relationship Managers. "
        "Handles client 360 view, portfolio analysis (live NAV/prices), "
        "SIP renewal tracking, compliance digest, email/WhatsApp drafts, "
        "and outbound voice calls in 11 Indian languages. "
        "All client communications are staged for RM approval - nothing sends automatically."
    ),
    "url": GATEWAY_URL,
    "provider": {
        "organization": "Agent Teams for Relationship Managers",
        "url": GATEWAY_URL,
    },
    "skills": [
        {
            "id": "morning_brief",
            "name": "Morning Brief",
            "description": "Daily compliance digest, SIP expiries, KYC alerts, client birthdays",
            "examples": [
                "Give me my morning brief for RM001",
                "What needs my attention today?",
            ],
            "tags": ["compliance", "portfolio", "alerts"],
        },
        {
            "id": "client_360",
            "name": "Client 360 View",
            "description": "Full client profile: accounts, portfolio, KYC, CRM history, credit score, cross-bank AA data",
            "examples": [
                "Show me the client card for Rahul",
                "What does C0001 portfolio look like?",
            ],
            "tags": ["client", "portfolio", "kyc"],
        },
        {
            "id": "portfolio_analysis",
            "name": "Portfolio Analysis",
            "description": "MF holdings with live NAV, SIP schedules, loan summary, demat holdings, expiry alerts",
            "examples": [
                "Show client C0001 complete portfolio",
                "Which clients have SIPs expiring this month?",
            ],
            "tags": ["portfolio", "sip", "nav", "loans"],
        },
        {
            "id": "communication_draft",
            "name": "Draft Communications",
            "description": "Draft emails, WhatsApp messages, and meeting invites for RM approval. Never sends automatically.",
            "examples": [
                "Draft a SIP renewal email for client C0001",
                "Send a WhatsApp reminder about KYC to Priya",
            ],
            "tags": ["email", "whatsapp", "draft", "approval"],
        },
        {
            "id": "voice_call",
            "name": "Voice Call in Regional Language",
            "description": "Stage and initiate AI voice calls in client preferred language (Hindi, Tamil, Telugu, Kannada, Malayalam, Marathi, Bengali, Gujarati, Punjabi, English)",
            "examples": [
                "Call Rahul about his SIP renewal in Hindi",
                "Call Priya in Tamil about her KYC",
            ],
            "tags": ["voice", "hindi", "tamil", "multilingual"],
        },
        {
            "id": "compliance_check",
            "name": "Compliance and KYC",
            "description": "KYC expiry alerts, AML flags, overdue EMIs, stale client contacts",
            "examples": [
                "Who has KYC expiring this month?",
                "Show me clients I have not contacted in 30 days",
            ],
            "tags": ["kyc", "compliance", "aml"],
        },
    ],
    "capabilities": {
        "streaming": True,
        "pushNotifications": False,
        "stateTransitionHistory": False,
    },
    "securitySchemes": {
        "google-id-token": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Google-issued ID token for Cloud Run IAM auth",
        },
    },
    "security": [{"google-id-token": []}],
    "defaultInputModes": ["text/plain"],
    "defaultOutputModes": ["text/plain"],
}


# ── Agent Card endpoint ───────────────────────────────────────────────────────

@app.get("/.well-known/agent-card.json")
def agent_card():
    """Agentspace fetches this to discover the agent's capabilities and skills.

    A2A v1.0 uses /.well-known/agent-card.json. We also expose the legacy
    /.well-known/agent.json path for older clients that haven't migrated.
    """
    return AGENT_CARD


@app.get("/.well-known/agent.json")
def agent_card_legacy():
    """Legacy pre-v1.0 path. Kept for back-compat; prefer agent-card.json."""
    return AGENT_CARD


# ── A2A Task Handler (non-streaming) ─────────────────────────────────────────

@app.post("/")
async def handle_task(request: Request):
    """
    A2A tasks/send and tasks/sendSubscribe handler.
    Agentspace POSTs a JSON-RPC task here when the RM sends a query.
    """
    try:
        body = await request.json()
        import sys
        print(f"Received A2A body: {body}", file=sys.stderr)
        method = body.get("method")
        task_id = body.get("params", {}).get("id", str(uuid.uuid4()))
        rm_user_id = _extract_user_id(body)
        session_id = _extract_or_create_session(body, rm_user_id)
        user_text = _extract_message_text(body)
    except Exception as e:
        log.exception("Failed to parse request body")
        return _error_response(None, str(uuid.uuid4()), f"Invalid request: {e}")

    if method in ["tasks/sendSubscribe", "message/stream"]:
        async def event_stream() -> AsyncGenerator[str, None]:
            try:
                # Working status update removed to avoid "message after first response" error
                # yield _sse_event({
                #     "jsonrpc": "2.0",
                #     "id": body.get("id"),
                #     "result": {
                #         "taskId": task_id,
                #         "contextId": session_id,
                #         "status": {
                #             "state": "working",
                #             "message": {
                #                 "messageId": str(uuid.uuid4()),
                #                 "role": "agent",
                #                 "parts": [{"text": "Processing your request..."}]
                #             }
                #         },
                #         "final": False
                #     }
                # })

                response_text = await _run_agent(rm_user_id, session_id, user_text)

                # Final event (Message)
                yield _sse_event({
                    "jsonrpc": "2.0",
                    "id": body.get("id"),
                    "result": {
                        "messageId": str(uuid.uuid4()),
                        "contextId": session_id,
                        "taskId": task_id,
                        "role": "agent",
                        "parts": [{"text": response_text}]
                    }
                })
            except Exception as e:
                log.exception("Error in event stream")
                yield _sse_event({
                    "jsonrpc": "2.0",
                    "id": body.get("id"),
                    "result": {
                        "taskId": task_id,
                        "contextId": session_id,
                        "status": {
                            "state": "failed",
                            "message": {
                                "messageId": str(uuid.uuid4()),
                                "role": "agent",
                                "parts": [{"text": f"Internal error: {e}"}]
                            }
                        },
                        "final": True
                    }
                })

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    else:
        # Unary request (tasks/send)
        if not user_text:
            return _error_response(body.get("id"), task_id, "Empty message received")

        response_text = await _run_agent(rm_user_id, session_id, user_text)

        return JSONResponse({
            "jsonrpc": "2.0",
            "id": body.get("id"),
            "result": {
                "id": task_id,
                "status": {"state": "completed"},
                "artifacts": [
                    {
                        "name": "response",
                        "parts": [{"kind": "text", "text": response_text}],
                    }
                ],
            },
        })


# ── A2A Streaming Handler (Server-Sent Events) ────────────────────────────────

@app.post("/stream")
async def handle_task_stream(request: Request):
    """
    A2A tasks/sendSubscribe handler — streams response as SSE.
    Agentspace uses this for real-time responses as the agent thinks.
    """
    try:
        body = await request.json()
        task_id = body.get("params", {}).get("id", str(uuid.uuid4()))
        rm_user_id = _extract_user_id(body)
        session_id = _extract_or_create_session(body, rm_user_id)
        user_text = _extract_message_text(body)
    except Exception as e:
        log.exception("Failed to parse request body for stream")
        async def error_stream() -> AsyncGenerator[str, None]:
            yield _sse_event({
                "jsonrpc": "2.0",
                "id": None,
                "result": {
                    "id": str(uuid.uuid4()),
                    "status": {
                        "state": "failed",
                        "message": {"role": "agent", "parts": [{"kind": "text", "text": f"Invalid request: {e}"}]},
                    },
                    "final": True,
                },
            })
        return StreamingResponse(error_stream(), media_type="text/event-stream")

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            # Working status update first
            yield _sse_event({
                "jsonrpc": "2.0",
                "id": body.get("id"),
                "result": {
                    "id": task_id,
                    "status": {
                        "state": "working",
                        "message": {
                            "role": "agent",
                            "parts": [{"kind": "text", "text": "Processing your request..."}],
                        },
                    },
                    "final": False,
                },
            })

            response_text = await _run_agent(rm_user_id, session_id, user_text)

            # Final event
            yield _sse_event({
                "jsonrpc": "2.0",
                "id": body.get("id"),
                "result": {
                    "id": task_id,
                    "status": {"state": "completed"},
                    "artifacts": [
                        {
                            "name": "response",
                            "parts": [{"kind": "text", "text": response_text}],
                        }
                    ],
                    "final": True,
                },
            })
        except Exception as e:
            log.exception("Error in event stream")
            yield _sse_event({
                "jsonrpc": "2.0",
                "id": body.get("id"),
                "result": {
                    "id": task_id,
                    "status": {
                        "state": "failed",
                        "message": {"role": "agent", "parts": [{"kind": "text", "text": f"Internal error: {e}"}]},
                    },
                    "final": True,
                },
            })

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "agent": "fsi-rm-orchestrator", "timestamp": datetime.utcnow().isoformat()}


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _run_agent(user_id: str, session_id: str, text: str) -> str:
    """Run the ADK orchestrator, collect the final response, persist to Memory Bank."""
    msg = Content(role="user", parts=[Part(text=text)])
    response_text = ""

    # Ensure session exists (Vertex AI Session Service is async)
    session = await session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id
    )
    if session is None:
        session = await session_service.create_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id
        )

    async for event in runner.run_async(
        user_id=user_id, session_id=session_id, new_message=msg
    ):
        if event.is_final_response() and event.content:
            response_text = "".join(
                p.text for p in event.content.parts if hasattr(p, "text")
            )

    # Persist completed turn into Memory Bank so future sessions can recall it.
    # Memory Bank auto-extracts topical facts (client preferences, RM decisions,
    # ongoing concerns) — no manual summarization needed.
    try:
        session = await session_service.get_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id
        )
        await memory_service.add_session_to_memory(session)
    except Exception as e:
        log.warning("Memory Bank persistence failed for %s: %s", session_id, e)

    return response_text or "I could not generate a response. Please try again."


def _extract_message_text(body: dict) -> str:
    parts = body.get("params", {}).get("message", {}).get("parts", [])
    return " ".join(
        p.get("text", "")
        for p in parts
        if p.get("kind") == "text" or p.get("type") == "text"  # accept both for back-compat
    ).strip()


def _extract_user_id(body: dict) -> str:
    params = body.get("params", {})
    meta = params.get("metadata", {})
    return meta.get("user_id") or meta.get("rm_id") or "rm-default"


def _extract_or_create_session(body: dict, user_id: str) -> str:
    # Gemini Enterprise sends contextId inside params.message (A2A multi-turn continuity)
    # Fall back to params.sessionId (our curl tests) or params.metadata fields
    params = body.get("params", {})
    message = params.get("message", {})
    meta = params.get("metadata", {})
    return (
        message.get("contextId")
        or params.get("sessionId")
        or meta.get("session_id")
        or meta.get("conversation_id")
        or f"session-{user_id}-{uuid.uuid4().hex[:8]}"
    )


def _error_response(rpc_id, task_id: str, message: str) -> JSONResponse:
    return JSONResponse({
        "jsonrpc": "2.0",
        "id": rpc_id,
        "result": {
            "id": task_id,
            "status": {
                "state": "failed",
                "message": {"role": "agent", "parts": [{"kind": "text", "text": message}]},
            },
        },
    }, status_code=200)  # A2A errors go in result, not HTTP status


def _sse_event(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
