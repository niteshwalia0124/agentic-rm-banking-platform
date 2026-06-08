"""
Portfolio & Investment Agent — analyzes MF holdings, SIPs, loans, stocks.
Proactively flags SIP expiries, NAV drops, LTV breaches.
Also calls external AWS AgentCore agents for AMFI NAV and live market data.

MCP discovery: Agent Registry in production, localhost fallback in local dev.
Pickle-safe: on Agent Engine unpickle, rebuilds with fresh Registry discovery.
"""

import os

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

from agents.common.registry import discover_mcp_toolset, handle_tool_error
from external_agents.a2a_client import a2a_call

SYSTEM_PROMPT = """
You are a portfolio analysis specialist for a bank RM.

━━━ QUERY TYPE — CHOOSE THE RIGHT TOOL PATTERN ━━━━━━━━━━━━━━━━━━━━━━━━━━━━

A) RM-WIDE QUERIES ("morning brief", "what needs attention", "which clients...",
   "all my SIPs", "all expiring"):
   → NEVER iterate per client. Use BULK tools:
     • get_clients_with_expiring_sips(rm_id, days_ahead=30)  ← ONE call for ALL SIPs
     • get_loan_summary is per-client — only call it for specific clients flagged above
   → Return a prioritised list across all clients, sorted by urgency.

B) SINGLE-CLIENT QUERIES ("show me Rekha's portfolio", "client C0040"):
   → Use per-client tools:
     • get_mf_holdings(client_id)
     • get_sip_schedule(client_id)
     • get_loan_summary(client_id)
     • get_demat_holdings(client_id)
   → Use get_mutual_fund_nav or get_market_data for live prices if asked.

━━━ SINGLE-CLIENT REPORT FORMAT ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Investment portfolio:
   - Mutual fund holdings (scheme, units, current value, gain/loss %)
   - SIP schedule (active SIPs, monthly amount, next debit, expiry)
   - Stock/demat holdings (top 5 by value)

2. Loan summary:
   - Active loans (type, outstanding, EMI, next due, LTV)
   - Overdue EMIs → flag ⚠️

3. Portfolio health:
   - Total AUM, asset allocation (equity/debt/liquid %)
   - SIPs expiring in 30 days → flag 🔔

4. Opportunities (for RM review only — do NOT share directly with client):
   - Underutilised capacity, refinancing, tax-saving if Q4

━━━ STYLE ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Amounts in ₹, lakh/crore notation (₹12.5L, ₹2.3Cr). Tables where helpful.
"""


async def get_mutual_fund_nav(fund_name: str) -> dict:
    """
    Get current NAV, 1Y/3Y/5Y returns, and AUM for a mutual fund from AMFI.
    Args:
        fund_name: Fund name or partial name (e.g. 'HDFC Mid-Cap Opportunities')
    Returns:
        Dict with nav, returns, aum, category fields.
    """
    return await a2a_call(os.getenv("AMFI_AGENT_URL", ""), fund_name)


async def get_market_data(symbol: str) -> dict:
    """
    Get live price, day change, 52-week range, and recent corporate actions for a stock.
    Args:
        symbol: NSE/BSE ticker symbol (e.g. 'RELIANCE', 'TCS', 'INFY')
    Returns:
        Dict with price, change_pct, week52_high, week52_low, volume fields.
    """
    return await a2a_call(os.getenv("MARKET_DATA_AGENT_URL", ""), symbol)


def _build_agent() -> LlmAgent:
    return _PickleSafeAgent(
        name="portfolio_agent",
        model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash"),
        description="Analyzes client portfolio: MF holdings, SIPs, loans, stocks, FDs. Flags expiries and opportunities. Uses live AMFI NAV and market data via AWS.",
        instruction=SYSTEM_PROMPT,
        tools=[
            discover_mcp_toolset(
                display_name="FSI-RM Portfolio MCP",
                fallback_env_var="PORTFOLIO_MCP_URL",
                fallback_default="http://localhost:8002",
            ),
            FunctionTool(get_mutual_fund_nav),
            FunctionTool(get_market_data),
        ],
        on_tool_error_callback=handle_tool_error,
    )


class _PickleSafeAgent(LlmAgent):
    """Rebuilds with fresh MCP toolset when unpickled by Agent Engine."""

    def __reduce__(self):
        return (_build_agent, ())

    def __deepcopy__(self, memo):
        return _build_agent()


portfolio_agent = _build_agent()
