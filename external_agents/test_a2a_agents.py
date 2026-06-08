"""
Smoke test for all 4 external A2A agents.
Run after deploying to AWS: python external_agents/test_a2a_agents.py

Tests:
  1. Agent Card fetch (/.well-known/agent.json)
  2. Sample task for each agent
  3. Response validation
"""

import asyncio
import os
import sys

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BOLD = "\033[1m"
RESET = "\033[0m"

AGENTS = {
    "AMFI NAV Agent": {
        "url_env": "AMFI_AGENT_URL",
        "test_query": "Get NAV for HDFC Mid-Cap Opportunities",
        "expected_keys": ["current_nav", "fund_name", "source"],
    },
    "Market Data Agent": {
        "url_env": "MARKET_DATA_AGENT_URL",
        "test_query": "Get current index levels for Nifty 50 and Sensex",
        "expected_keys": ["indices", "source"],
    },
    "Credit Bureau Agent": {
        "url_env": "CREDIT_BUREAU_AGENT_URL",
        "test_query": "Credit report for client C0001",
        "expected_keys": ["credit_score", "risk_band", "trades"],
    },
    "Account Aggregator Agent": {
        "url_env": "ACCOUNT_AGGREGATOR_AGENT_URL",
        "test_query": "Full financial profile for client C0001",
        "expected_keys": ["summary", "savings_accounts", "insight"],
    },
}


async def test_agent(name: str, config: dict) -> bool:
    from external_agents.a2a_client import a2a_call, get_agent_card

    url = os.getenv(config["url_env"], "")
    print(f"\n{BOLD}▶ {name}{RESET}")

    if not url:
        print(f"  {YELLOW}⚠ SKIPPED — {config['url_env']} not set in .env{RESET}")
        print(f"  Deploy with: bash external_agents/aws_deploy.sh")
        return True  # not a failure — just not deployed yet

    # 1. Fetch agent card
    card = await get_agent_card(url)
    if "error" in card:
        print(f"  {RED}✗ Agent Card fetch failed: {card['error']}{RESET}")
        return False
    print(f"  {GREEN}✓{RESET} Agent Card: {card.get('name')} — {len(card.get('skills', []))} skills")

    # 2. Send test task
    result = await a2a_call(url, config["test_query"])
    if "error" in result:
        print(f"  {RED}✗ Task failed: {result['error']}{RESET}")
        return False

    # 3. Validate expected keys
    missing = [k for k in config["expected_keys"] if k not in result]
    if missing:
        print(f"  {RED}✗ Missing keys in response: {missing}{RESET}")
        print(f"  Got keys: {list(result.keys())}")
        return False

    print(f"  {GREEN}✓{RESET} Task response valid — keys: {list(result.keys())[:5]}")

    # Show a highlight from the response
    if "current_nav" in result:
        print(f"  → NAV: ₹{result['current_nav']}  ({result.get('fund_name', '')})")
    if "indices" in result:
        for idx, data in result["indices"].items():
            print(f"  → {idx}: {data.get('level', '?')} ({data.get('change_pct', '?')}%)")
    if "credit_score" in result:
        print(f"  → Credit Score: {result['credit_score']} ({result.get('risk_band', '')})")
    if "insight" in result:
        print(f"  → AA Insight: {result['insight']}")

    return True


async def main():
    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}  Agent Teams for Relationship Managers — External A2A Agent Health Check{RESET}")
    print(f"{BOLD}{'='*60}{RESET}")

    results = []
    for name, config in AGENTS.items():
        passed = await test_agent(name, config)
        results.append((name, passed))

    print(f"\n{BOLD}{'='*60}{RESET}")
    deployed = [r for r in results if r[1]]
    print(f"  {len(deployed)}/{len(results)} agents passing")

    unconfigured = [
        name for name, config in AGENTS.items()
        if not os.getenv(config["url_env"], "")
    ]
    if unconfigured:
        print(f"\n  {YELLOW}To deploy missing agents:{RESET}")
        print(f"  bash external_agents/aws_deploy.sh")

    failed = [name for name, passed in results if not passed]
    if failed:
        print(f"\n  {RED}Failed: {failed}{RESET}")
        sys.exit(1)
    else:
        print(f"\n  {GREEN}{BOLD}All deployed agents responding correctly.{RESET}")


if __name__ == "__main__":
    asyncio.run(main())
