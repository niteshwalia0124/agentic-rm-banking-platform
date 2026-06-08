"""
Memory Bank cross-session continuity demo.

Proves the "remember Rahul" demo moment:
  Session 1:  RM tells the agent about Rahul's tax-saving interest
  → Session is persisted to Vertex AI Memory Bank
  → Memory Bank auto-extracts topical facts about Rahul

  Session 2 (separate, hours/days later):  RM asks about Rahul
  → PreloadMemoryTool retrieves prior facts
  → Agent's response references the prior conversation explicitly

Run:
  export AGENT_ENGINE_ID=projects/.../locations/asia-south1/reasoningEngines/...
  python tests/test_memory_continuity.py
"""

import asyncio
import os
import sys
import uuid

from google.adk.memory.vertex_ai_memory_bank_service import VertexAiMemoryBankService
from google.adk.runners import Runner
from google.adk.sessions import VertexAiSessionService
from google.genai.types import Content, Part

from agents.orchestrator.agent import orchestrator

GREEN = "\033[92m"
YELLOW = "\033[93m"
BOLD = "\033[1m"
RESET = "\033[0m"

APP_NAME = "fsi-rm-memory-demo"
RM_USER_ID = "rm001@yourbank.com"

GCP_PROJECT = os.environ["GCP_PROJECT"]
GCP_LOCATION = os.getenv("GCP_LOCATION", "asia-south1")
AGENT_ENGINE_ID = os.environ["AGENT_ENGINE_ID"]


async def run_turn(runner: Runner, session_id: str, text: str) -> str:
    msg = Content(role="user", parts=[Part(text=text)])
    response = ""
    async for event in runner.run_async(
        user_id=RM_USER_ID, session_id=session_id, new_message=msg
    ):
        if event.is_final_response() and event.content:
            response = "".join(p.text for p in event.content.parts if hasattr(p, "text"))
    return response


async def main():
    session_svc = VertexAiSessionService(
        project=GCP_PROJECT, location=GCP_LOCATION, agent_engine_id=AGENT_ENGINE_ID
    )
    memory_svc = VertexAiMemoryBankService(
        project=GCP_PROJECT, location=GCP_LOCATION, agent_engine_id=AGENT_ENGINE_ID
    )
    runner = Runner(
        agent=orchestrator,
        app_name=APP_NAME,
        session_service=session_svc,
        memory_service=memory_svc,
    )

    # ── SESSION 1 ─────────────────────────────────────────────────────────────
    print(f"\n{BOLD}━━━ Session 1 (Monday morning) ━━━{RESET}")
    s1 = f"sess-{uuid.uuid4()}"
    await session_svc.create_session(app_name=APP_NAME, user_id=RM_USER_ID, session_id=s1)

    q1 = (
        "I just met Rahul Gupta (client C0001). He asked about tax-saving for FY26 — "
        "wants to invest ₹1.5L under 80C before March. He's risk-tolerant, prefers ELSS over PPF."
    )
    print(f"\n{YELLOW}RM:{RESET} {q1}")
    a1 = await run_turn(runner, s1, q1)
    print(f"{GREEN}Agent:{RESET} {a1[:400]}...")

    # Persist Session 1 to Memory Bank — this is what gateway/a2a_server.py does
    session_obj = await session_svc.get_session(
        app_name=APP_NAME, user_id=RM_USER_ID, session_id=s1
    )
    await memory_svc.add_session_to_memory(session_obj)
    print(f"\n{BOLD}→ Session 1 persisted to Memory Bank{RESET}")

    # ── SESSION 2 (NEW SESSION, days later) ──────────────────────────────────
    print(f"\n{BOLD}━━━ Session 2 (Thursday — completely new session) ━━━{RESET}")
    s2 = f"sess-{uuid.uuid4()}"
    await session_svc.create_session(app_name=APP_NAME, user_id=RM_USER_ID, session_id=s2)

    q2 = "Any updates I should share with Rahul?"
    print(f"\n{YELLOW}RM:{RESET} {q2}")
    a2 = await run_turn(runner, s2, q2)
    print(f"{GREEN}Agent:{RESET} {a2}")

    # ── Validation ────────────────────────────────────────────────────────────
    print(f"\n{BOLD}━━━ Continuity check ━━━{RESET}")
    recall_signals = ["tax", "80c", "elss", "march", "1.5", "fy26"]
    hits = [s for s in recall_signals if s in a2.lower()]

    if hits:
        print(f"{GREEN}✓ Memory recall succeeded — referenced: {hits}{RESET}")
        print(f"  Agent recalled Session 1 context without being told.")
        sys.exit(0)
    else:
        print(f"{YELLOW}✗ Agent did not reference prior session facts.{RESET}")
        print(f"  Memory Bank may need a few seconds to index — re-run shortly.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
