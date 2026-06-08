"""
Layer 6: Demo Rehearsal Script
Runs all 5 demo scenes sequentially with timing measurements.
Use this the day before a bank presentation to validate everything works.

Run: python tests/demo_rehearsal.py
Output: colored pass/fail per scene + total time
"""

import time
import sys
import os

# Force real data
os.environ.setdefault("GCP_PROJECT", "your-project")
os.environ.setdefault("BQ_DATASET", "fsi_rm_poc")

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
BOLD = "\033[1m"
RESET = "\033[0m"

results = []


def scene(name: str, expected_keywords: list, query: str, critical: bool = False):
    """Run a demo scene and validate it contains expected keywords."""
    print(f"\n{BLUE}{BOLD}▶ {name}{RESET}")
    print(f"  Query: {YELLOW}\"{query}\"{RESET}")

    start = time.time()
    try:
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.genai.types import Content, Part
        from agents.orchestrator.agent import orchestrator

        runner = Runner(
            agent=orchestrator,
            app_name="demo-rehearsal",
            session_service=InMemorySessionService(),
        )
        session = runner.session_service.create_session(
            app_name="demo-rehearsal", user_id="demo-rm-RM001"
        )
        msg = Content(role="user", parts=[Part(text=query)])
        response = ""
        for event in runner.run(
            user_id="demo-rm-RM001",
            session_id=session.id,
            new_message=msg,
        ):
            if event.is_final_response() and event.content:
                response = "".join(
                    p.text for p in event.content.parts if hasattr(p, "text")
                )

        elapsed = time.time() - start
        missing = [k for k in expected_keywords if k.lower() not in response.lower()]

        if missing:
            status = f"{RED}✗ FAIL{RESET}"
            detail = f"Missing: {missing}"
            passed = False
        else:
            status = f"{GREEN}✓ PASS{RESET}"
            detail = f"All {len(expected_keywords)} keywords found"
            passed = True

        print(f"  {status}  ({elapsed:.1f}s)  {detail}")
        if not passed and len(response) > 0:
            print(f"  Response preview: {response[:200]}...")

        results.append({
            "name": name, "passed": passed, "elapsed": elapsed,
            "critical": critical, "response_len": len(response)
        })

    except Exception as e:
        elapsed = time.time() - start
        print(f"  {RED}✗ ERROR{RESET}  ({elapsed:.1f}s)  {e}")
        results.append({
            "name": name, "passed": False, "elapsed": elapsed,
            "critical": critical, "error": str(e)
        })


def mcp_health_check():
    """Quick check all 4 MCP servers are responding."""
    import httpx
    servers = {
        "core-banking-mcp": os.getenv("CORE_BANKING_MCP_URL", "http://localhost:8001"),
        "portfolio-mcp":    os.getenv("PORTFOLIO_MCP_URL",    "http://localhost:8002"),
        "comms-mcp":        os.getenv("COMMS_MCP_URL",        "http://localhost:8003"),
        "compliance-mcp":   os.getenv("COMPLIANCE_MCP_URL",   "http://localhost:8004"),
        "voice-mcp":        os.getenv("VOICE_MCP_URL",        "http://localhost:8005"),
    }
    print(f"\n{BOLD}=== MCP Server Health Check ==={RESET}")
    all_up = True
    for name, url in servers.items():
        try:
            r = httpx.get(f"{url}/health", timeout=3)
            print(f"  {GREEN}✓{RESET} {name}")
        except Exception:
            print(f"  {RED}✗{RESET} {name} — not responding at {url}")
            all_up = False
    if not all_up:
        print(f"\n{RED}Some MCP servers are down. Run: ./scripts/start_local.sh{RESET}")
    return all_up


def main():
    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}  Agent Teams for Relationship Managers — Demo Rehearsal{RESET}")
    print(f"{BOLD}{'='*60}{RESET}")

    # Health checks first
    mcp_ok = mcp_health_check()
    if not mcp_ok:
        print(f"\n{YELLOW}⚠ Continuing with degraded MCP connectivity...{RESET}")

    print(f"\n{BOLD}=== Scene Tests ==={RESET}")

    # ── Scene 1: Morning Brief ────────────────────────────────────────────
    scene(
        name="Scene 1 — Morning Brief",
        query="Give me my morning brief and compliance digest for RM001",
        expected_keywords=["kyc", "sip", "urgent"],
        critical=True,
    )

    # ── Scene 2a: Client Lookup ───────────────────────────────────────────
    scene(
        name="Scene 2a — Client Lookup by Name",
        query="Show me the full client card for Rahul",
        expected_keywords=["account", "segment", "₹"],
        critical=True,
    )

    # ── Scene 2b: Portfolio Deep Dive ─────────────────────────────────────
    scene(
        name="Scene 2b — Portfolio Deep Dive",
        query="Show me client C0001's complete portfolio — MF, loans, SIPs",
        expected_keywords=["fund", "sip", "₹", "loan"],
        critical=True,
    )

    # ── Scene 3a: Email Draft ─────────────────────────────────────────────
    scene(
        name="Scene 3a — Email Draft (SIP Renewal)",
        query="Draft a SIP renewal email for client C0001",
        expected_keywords=["subject", "sip", "approve"],
        critical=True,
    )

    # ── Scene 3b: Guardrail Check ─────────────────────────────────────────
    scene(
        name="Scene 3b — Guardrail: No Auto-Send",
        query="Send an email to all my clients about new FD rates",
        expected_keywords=["draft", "approve"],  # must NOT auto-send
        critical=True,
    )

    # ── Scene 4: Voice Call Staging ───────────────────────────────────────
    scene(
        name="Scene 4 — Voice Call Staging (Hindi SIP Renewal)",
        query="Call Rahul about his SIP renewal in Hindi",
        expected_keywords=["approve", "call", "hindi"],
        critical=True,
    )

    # ── Scene 5: Top Clients ──────────────────────────────────────────────
    scene(
        name="Scene 5 — Top Clients by AUM",
        query="List my top 10 clients by AUM for RM001",
        expected_keywords=["₹", "aum"],
        critical=False,
    )

    # ── Scene 6: Compliance Digest ────────────────────────────────────────
    scene(
        name="Scene 6 — Compliance Digest",
        query="Who has KYC expiring this month across my RM001 clients?",
        expected_keywords=["kyc", "expir"],
        critical=False,
    )

    # ── Summary ───────────────────────────────────────────────────────────
    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}  REHEARSAL SUMMARY{RESET}")
    print(f"{BOLD}{'='*60}{RESET}")

    passed = [r for r in results if r["passed"]]
    failed = [r for r in results if not r["passed"]]
    critical_failures = [r for r in failed if r.get("critical")]
    total_time = sum(r["elapsed"] for r in results)

    for r in results:
        icon = f"{GREEN}✓{RESET}" if r["passed"] else f"{RED}✗{RESET}"
        crit = f" {RED}[CRITICAL]{RESET}" if r.get("critical") and not r["passed"] else ""
        print(f"  {icon} {r['name']} ({r['elapsed']:.1f}s){crit}")

    print(f"\n  Passed: {GREEN}{len(passed)}/{len(results)}{RESET}")
    print(f"  Total time: {total_time:.1f}s")
    print(f"  Avg response: {total_time/len(results):.1f}s per scene")

    if critical_failures:
        print(f"\n{RED}{BOLD}  ✗ {len(critical_failures)} CRITICAL FAILURE(S) — DO NOT DEMO TODAY{RESET}")
        for r in critical_failures:
            print(f"    → {r['name']}")
        sys.exit(1)
    elif failed:
        print(f"\n{YELLOW}{BOLD}  ⚠ {len(failed)} non-critical failure(s) — demo with caution{RESET}")
    else:
        print(f"\n{GREEN}{BOLD}  ✓ ALL SCENES PASSED — READY TO DEMO!{RESET}")


if __name__ == "__main__":
    main()
