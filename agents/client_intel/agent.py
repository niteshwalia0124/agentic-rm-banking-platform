"""
Client Intelligence Agent — fetches 360° client view.
Pulls from core-banking-mcp (accounts, transactions, KYC, CRM).
Also calls external AWS AgentCore agents for credit bureau and Account Aggregator data.

MCP discovery: Agent Registry in production, localhost fallback in local dev.
Pickle-safe: on Agent Engine unpickle, rebuilds with fresh Registry discovery.
"""

import os

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

from agents.common.registry import discover_mcp_toolset, handle_tool_error
from external_agents.a2a_client import a2a_call

SYSTEM_PROMPT = """
You are a client intelligence specialist. Given a client name or ID, fetch and summarize:
1. Account summary (savings, current, FD balances)
2. Recent significant transactions (last 30 days, amounts > ₹1 lakh)
3. KYC status and document expiry
4. Last interaction date and notes from CRM
5. Client segment (HNI / Mass Affluent / SME) and risk profile
6. Credit score and cross-bank Account Aggregator data (if available)
7. Next best action recommendation

Use get_cibil_report to fetch CIBIL/credit data when a PAN is available.
Use get_aa_holdings to get cross-bank holdings and liabilities via Account Aggregator (with client consent).

Format the output as a clean "Client Card" with sections. Be concise.
Flag any alerts (KYC expiry, missed EMI, large outflow) with ⚠️
"""


async def get_cibil_report(pan: str) -> dict:
    """
    Fetch credit bureau report (CIBIL score, credit history, active loans) for a client
    via the external AWS AgentCore credit bureau agent.
    Args:
        pan: PAN card number of the client (e.g. 'ABCDE1234F')
    Returns:
        Dict with cibil_score, active_loans, credit_utilization, payment_history fields.
    """
    return await a2a_call(os.getenv("CREDIT_BUREAU_AGENT_URL", ""), pan)


async def get_aa_holdings(customer_id: str) -> dict:
    """
    Fetch cross-bank financial data via the RBI Account Aggregator framework
    using the external AWS AgentCore account aggregator agent.
    Args:
        customer_id: Bank's internal customer ID or mobile number
    Returns:
        Dict with external_accounts, total_assets, total_liabilities, consent_status fields.
    """
    return await a2a_call(os.getenv("ACCOUNT_AGGREGATOR_AGENT_URL", ""), customer_id)


def _build_agent() -> LlmAgent:
    return _PickleSafeAgent(
        name="client_intel_agent",
        model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash"),
        description="Fetches complete 360° client view: accounts, KYC, CRM history, segment, credit score via CIBIL, and cross-bank data via Account Aggregator.",
        instruction=SYSTEM_PROMPT,
        tools=[
            discover_mcp_toolset(
                display_name="FSI-RM Core Banking MCP",
                fallback_env_var="CORE_BANKING_MCP_URL",
                fallback_default="http://localhost:8001",
            ),
            FunctionTool(get_cibil_report),
            FunctionTool(get_aa_holdings),
        ],
        on_tool_error_callback=handle_tool_error,
    )


class _PickleSafeAgent(LlmAgent):
    """Rebuilds with fresh MCP toolset when unpickled by Agent Engine."""

    def __reduce__(self):
        return (_build_agent, ())

    def __deepcopy__(self, memo):
        return _build_agent()


client_intel_agent = _build_agent()
