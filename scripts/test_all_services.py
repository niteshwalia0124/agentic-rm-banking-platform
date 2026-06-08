"""
Comprehensive test of all FSI-RM services via the deployed Orchestrator:
  - 5 MCP servers (Core Banking, Portfolio, Comms, Compliance, Voice)
  - 4 AWS Bedrock AgentCore A2A agents (AMFI, Market Data, Credit Bureau, Account Aggregator)

Each test sends a query through the orchestrator, which routes to the correct
sub-agent and MCP/A2A service. Results show tool calls + final response.
"""

import asyncio
import os
import sys
import textwrap
import time

import vertexai
from vertexai import agent_engines

PROJECT = os.getenv("GCP_PROJECT", "<YOUR_GCP_PROJECT>")
LOCATION = os.getenv("GCP_LOCATION", "us-east1")
AGENT_ENGINE_ID = os.getenv("AGENT_ENGINE_ID", "9054979632037625856")

# ── Test cases ────────────────────────────────────────────────────────────────

TESTS = [
    # ── MCP Tests ──────────────────────────────────────────────────────────────
    {
        "id": "MCP-1",
        "service": "Core Banking MCP",
        "query": (
            "Use the core banking MCP to fetch account details and recent "
            "transactions for customer ID CUST001. Show balance and last 3 transactions."
        ),
    },
    {
        "id": "MCP-2",
        "service": "Portfolio MCP",
        "query": (
            "Use the portfolio MCP to list all mutual fund SIP schedules for "
            "customer CUST001. Show fund name, monthly amount, and next due date."
        ),
    },
    {
        "id": "MCP-3",
        "service": "Communications MCP",
        "query": (
            "Use the communications MCP to draft a short portfolio review email "
            "for client Rahul Gupta. Subject: Q2 Portfolio Review. Keep it to 3 bullet points."
        ),
    },
    {
        "id": "MCP-4",
        "service": "Compliance MCP",
        "query": (
            "Use the compliance MCP to check KYC expiry status for all clients "
            "and flag any with KYC expiring in the next 30 days. Also check for any AML alerts."
        ),
    },
    {
        "id": "MCP-5",
        "service": "Voice MCP",
        "query": (
            "Use the voice MCP to prepare a call script for contacting client "
            "Priya Sharma about her SIP renewal. The call should be in Hindi. "
            "Show me the script that would be used — do NOT actually place the call."
        ),
    },
    # ── AWS A2A Agent Tests ────────────────────────────────────────────────────
    {
        "id": "A2A-1",
        "service": "AMFI Agent (AWS Bedrock)",
        "query": (
            "Use the AMFI agent to get the current NAV and 1-year return for "
            "HDFC Mid-Cap Opportunities Fund. Route this through the portfolio agent."
        ),
    },
    {
        "id": "A2A-2",
        "service": "Market Data Agent (AWS Bedrock)",
        "query": (
            "Use the market data agent to get the current price, 52-week high/low, "
            "and today's change for Reliance Industries (NSE: RELIANCE). "
            "Route this through the portfolio agent."
        ),
    },
    {
        "id": "A2A-3",
        "service": "Credit Bureau Agent (AWS Bedrock)",
        "query": (
            "Use the credit bureau agent to get a credit score summary for "
            "PAN number ABCDE1234F. Include CIBIL score and any active loan accounts. "
            "Route this through the client intel agent."
        ),
    },
    {
        "id": "A2A-4",
        "service": "Account Aggregator Agent (AWS Bedrock)",
        "query": (
            "Use the account aggregator agent to get a consolidated financial "
            "summary for customer ID CUST001 — all accounts across all banks. "
            "Route this through the client intel agent."
        ),
    },
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def print_header(title: str):
    print(f"\n{'═'*65}")
    print(f"  {title}")
    print(f"{'═'*65}")


def print_section(label: str, content: str = ""):
    print(f"\n{'─'*65}")
    if content:
        print(f"  {label}: {content}")
    else:
        print(f"  {label}")
    print(f"{'─'*65}")


def truncate(text: str, max_len: int = 300) -> str:
    return text if len(text) <= max_len else text[:max_len] + "…"


async def run_test(remote_app, test: dict, user_id: str) -> dict:
    result = {
        "id": test["id"],
        "service": test["service"],
        "status": "UNKNOWN",
        "tool_calls": [],
        "response": "",
        "duration_s": 0,
    }

    t0 = time.time()
    try:
        session = await remote_app.async_create_session(user_id=user_id)
        session_id = session["id"]

        response_parts = []
        async for event in remote_app.async_stream_query(
            user_id=user_id,
            session_id=session_id,
            message=test["query"],
        ):
            parts = event.get("content", {}).get("parts", [])
            for part in parts:
                if "text" in part:
                    response_parts.append(part["text"])
                elif "function_call" in part:
                    fc = part["function_call"]
                    result["tool_calls"].append(fc.get("name", "?"))
                elif "function_response" in part:
                    pass  # captured via tool_calls above

        result["response"] = "".join(response_parts)
        result["status"] = "PASS" if result["response"] else "EMPTY"

    except Exception as exc:
        result["status"] = "FAIL"
        result["response"] = str(exc)

    result["duration_s"] = round(time.time() - t0, 1)
    return result


async def main():
    print_header("Agent Teams for Relationship Managers — Full Service Test Suite")
    print(f"  Project  : {PROJECT}")
    print(f"  Location : {LOCATION}")
    print(f"  Engine   : {AGENT_ENGINE_ID}")
    print(f"  Tests    : {len(TESTS)} ({sum(1 for t in TESTS if t['id'].startswith('MCP'))} MCP + "
          f"{sum(1 for t in TESTS if t['id'].startswith('A2A'))} A2A)")

    vertexai.init(project=PROJECT, location=LOCATION)
    resource_name = (
        f"projects/{PROJECT}/locations/{LOCATION}/reasoningEngines/{AGENT_ENGINE_ID}"
    )
    remote_app = agent_engines.get(resource_name)
    print(f"\n  Connected to Agent Engine ✓")

    results = []
    for i, test in enumerate(TESTS, 1):
        user_id = f"test-{test['id'].lower()}"
        print(f"\n[{i}/{len(TESTS)}] {test['id']} — {test['service']}")
        print(f"  Query: {truncate(test['query'], 100)}")
        print(f"  Running...", end="", flush=True)

        result = await run_test(remote_app, test, user_id)
        results.append(result)

        status_icon = "✅" if result["status"] == "PASS" else ("⚠️ " if result["status"] == "EMPTY" else "❌")
        print(f"\r  {status_icon} {result['status']} ({result['duration_s']}s)  Tools: {result['tool_calls'] or ['none']}")
        if result["response"]:
            wrapped = textwrap.fill(result["response"][:400], width=62, initial_indent="  ", subsequent_indent="  ")
            print(wrapped)
        elif result["status"] == "FAIL":
            print(f"  ERROR: {truncate(result['response'], 200)}")

    # ── Summary table ──────────────────────────────────────────────────────────
    print_header("Test Results Summary")
    print(f"  {'ID':<8} {'Service':<35} {'Status':<8} {'Time':>6}  Tools Called")
    print(f"  {'─'*8} {'─'*35} {'─'*8} {'─'*6}  {'─'*20}")
    for r in results:
        icon = "✅" if r["status"] == "PASS" else ("⚠️ " if r["status"] == "EMPTY" else "❌")
        tools = ", ".join(r["tool_calls"]) if r["tool_calls"] else "—"
        print(f"  {r['id']:<8} {r['service']:<35} {icon} {r['status']:<6} {r['duration_s']:>5}s  {truncate(tools, 40)}")

    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    empty = sum(1 for r in results if r["status"] == "EMPTY")
    print(f"\n  Total: {len(results)}  ✅ {passed} passed  ⚠️  {empty} empty  ❌ {failed} failed")
    print()

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
