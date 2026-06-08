"""End-to-end backend test: fire one prompt per MCP server + per AWS A2A agent,
check which tools actually fired, and print a pass/fail table."""
import os
import sys
import time

import vertexai
from vertexai import agent_engines

ENGINE = "projects/1058427839055/locations/us-east1/reasoningEngines/7519252159104286720"
USER_ID = "test-rm-e2e"

# (label, expected-substring-in-leaf-tool-name, prompt)
# Substrings match against the leaf tool name (e.g. FSI_RM_Core_Banking_MCP_get_balance
# matches "Core_Banking_MCP"; A2A tool wrappers match their function name).
TESTS = [
    # ── 5 GCP MCP servers ──────────────────────────────────────────────
    ("MCP: Core Banking",  "Core_Banking_MCP",
        "Show me the account balance and last 3 transactions for client C0001 (Meena Iyer)."),
    ("MCP: Portfolio",     "Portfolio_MCP",
        "Show me all demat holdings and SIP mandates for client C0002 (Rajesh Gupta)."),
    ("MCP: Comms",         "Communications_MCP",
        "Draft an email to client C0003 (Sanjay Patel) about his upcoming SIP renewal next month."),
    ("MCP: Compliance",    "Compliance_MCP",
        "Check the KYC status and any AML flags for client C0004 (Nisha Sharma)."),
    ("MCP: Voice",         "Voice_MCP",
        "Schedule an outbound voice call in Hindi to client C0005 (Amit Reddy) for tomorrow at 11am."),
    # ── 4 AWS A2A agents on Bedrock AgentCore (FunctionTool wrappers) ──
    ("A2A: AMFI",          "mutual_fund_nav",
        "What is the current NAV and 1-year return for HDFC Mid-Cap Opportunities Fund? Use the AMFI agent."),
    ("A2A: Market Data",   "market_data",
        "Get the current NSE market price and today's change for RELIANCE stock. Use the market data agent."),
    ("A2A: Credit Bureau", "credit_bureau",
        "Pull the credit bureau report for PAN ABCDE1234F via the credit bureau agent."),
    ("A2A: Account Aggr.", "account_aggregator",
        "Fetch cross-bank holdings via Account Aggregator for client C0001 (consent ID: AA-CONS-0001)."),
]


def run_one(engine, label: str, expected_tool: str, prompt: str) -> dict:
    """Stream a single query; return summary dict.

    The orchestrator routes to sub-agents (which appear as function_calls at
    the rm_orchestrator level). Each sub-agent then makes its own
    function_calls (MCP tool calls, A2A FunctionTool calls) under its own
    author. We collect ALL function_calls across every author so we can
    match against the actual leaf tool (e.g. FSI_RM_Core_Banking_MCP_*).
    """
    tools_called: list[str] = []
    tool_errors: list[str] = []
    final_text = ""
    sub_agents: set[str] = set()
    t0 = time.time()

    try:
        for ev in engine.stream_query(user_id=USER_ID, message=prompt):
            content = ev.get("content") or {}
            author = ev.get("author") or ""
            for part in content.get("parts", []) or []:
                if "function_call" in part:
                    fname = part["function_call"].get("name", "?")
                    tools_called.append(f"[{author}] {fname}")
                    if fname.endswith("_agent"):
                        sub_agents.add(fname)
                if "function_response" in part:
                    fname = part["function_response"].get("name", "?")
                    resp = part["function_response"].get("response") or {}
                    err_text = ""
                    if isinstance(resp, dict):
                        if resp.get("error"):
                            err_text = str(resp.get("error"))[:200]
                        elif resp.get("status") == "error":
                            err_text = str(resp)[:200]
                    if err_text:
                        tool_errors.append(f"{fname}: {err_text}")
                if "text" in part and author == "rm_orchestrator":
                    final_text = part["text"]
    except Exception as e:
        return {
            "label": label, "ok": False, "duration": time.time() - t0,
            "tools": tools_called, "errors": [f"STREAM_ERROR: {e}"],
            "sub_agents": list(sub_agents), "expected_hit": False,
            "final": "",
        }

    expected_hit = any(expected_tool.lower() in t.lower() for t in tools_called)
    return {
        "label": label,
        "ok": expected_hit and not tool_errors,
        "duration": time.time() - t0,
        "tools": tools_called,
        "errors": tool_errors,
        "sub_agents": sorted(sub_agents),
        "expected_hit": expected_hit,
        "final": final_text[:300],
    }


def main():
    vertexai.init(project="<YOUR_GCP_PROJECT>", location="us-east1")
    engine = agent_engines.get(ENGINE)
    print(f"Engine: {ENGINE}\n")

    results = []
    for label, expected, prompt in TESTS:
        print(f"━━━ {label} ━━━")
        print(f"  prompt: {prompt[:90]}{'…' if len(prompt) > 90 else ''}")
        r = run_one(engine, label, expected, prompt)
        results.append(r)
        status = "✓ PASS" if r["ok"] else ("◐ PARTIAL" if r["expected_hit"] else "✗ FAIL")
        print(f"  {status}  {r['duration']:.1f}s  sub_agents={r['sub_agents']}")
        print(f"  tools_called: {r['tools']}")
        if r["errors"]:
            for e in r["errors"][:3]:
                print(f"  ERROR: {e[:200]}")
        if r["final"]:
            print(f"  reply: {r['final'][:200]}")
        print()

    # ── Final table ─────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print(f"{'BACKEND':<22} {'STATUS':<10} {'TIME':<7} {'EXPECTED TOOL FIRED'}")
    print("=" * 78)
    for r in results:
        status = "PASS" if r["ok"] else ("PARTIAL" if r["expected_hit"] else "FAIL")
        print(f"{r['label']:<22} {status:<10} {r['duration']:>5.1f}s  {r['expected_hit']}")
    passes = sum(1 for r in results if r["ok"])
    print("=" * 78)
    print(f"  {passes}/{len(results)} passed")


if __name__ == "__main__":
    main()
