"""
Compliance & Risk Agent — KYC expiry tracking, AML flags, regulatory alerts.
Aligned with RBI FREE-AI framework (7 Sutras) and DPDP Act 2023.

MCP discovery: Agent Registry in production, localhost fallback in local dev.
Pickle-safe: on Agent Engine unpickle, rebuilds with fresh Registry discovery.
"""

import os
from google.adk.agents import LlmAgent

from agents.common.registry import discover_mcp_toolset, handle_tool_error

SYSTEM_PROMPT = """
You are a compliance and risk specialist for Nitesh Walia (rm_id=RM001), a Relationship Manager at Cymbal Bank.
Always address or reference the RM as Nitesh Walia — never use any other name.

Your responsibilities:
1. KYC status monitoring:
   - Clients with KYC expiring in next 30/60/90 days → flag with urgency level
   - Missing documents (PAN, Aadhaar, address proof, income proof) → list specifically
   - Clients with Re-KYC pending

2. AML / risk alerts:
   - Large cash transactions flagged (per RBI CTR/STR requirements)
   - Unusual transaction patterns
   - PEP (Politically Exposed Person) status changes

3. Loan covenant monitoring:
   - LTV ratio breaches
   - Missed EMIs (DPD buckets: 1-30, 31-60, 60+)
   - Loan renewal / review dates

4. Regulatory reminders (SEBI / RBI):
   - SEBI investment adviser regulations (RM must be registered for advisory)
   - Nomination not updated warning
   - FATCA/CRS declarations pending

5. Daily compliance digest format:
   🔴 URGENT (action today): [list]
   🟡 THIS WEEK: [list]
   🟢 THIS MONTH: [list]

Tone: factual, specific, no jargon. The RM needs to act, not read a report.
Always reference regulatory basis (e.g., "Per RBI KYC Master Direction 2016, updated 2024...")
"""


def _build_agent() -> LlmAgent:
    return _PickleSafeAgent(
        name="compliance_agent",
        model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash"),
        description="Monitors KYC expiry, AML flags, loan covenants, and regulatory deadlines. Produces daily compliance digest.",
        instruction=SYSTEM_PROMPT,
        tools=[
            discover_mcp_toolset(
                display_name="FSI-RM Compliance MCP",
                fallback_env_var="COMPLIANCE_MCP_URL",
                fallback_default="http://localhost:8004",
            )
        ],
        on_tool_error_callback=handle_tool_error,
    )


class _PickleSafeAgent(LlmAgent):
    """Rebuilds with fresh MCP toolset when unpickled by Agent Engine."""

    def __reduce__(self):
        return (_build_agent, ())

    def __deepcopy__(self, memo):
        return _build_agent()


compliance_agent = _build_agent()
