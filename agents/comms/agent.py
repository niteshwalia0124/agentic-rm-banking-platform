"""
Communication & Scheduling Agent — drafts emails, schedules meetings, composes WhatsApp.
CRITICAL: Always creates DRAFTS. Never sends without explicit RM approval.

MCP discovery: Agent Registry in production, localhost fallback in local dev.
Pickle-safe: on Agent Engine unpickle, rebuilds with fresh Registry discovery.
"""

import os
from google.adk.agents import LlmAgent

from agents.common.registry import discover_mcp_toolset, handle_tool_error

SYSTEM_PROMPT = """
You are a communication specialist for a bank Relationship Manager.
You draft professional, personalized client communications.

CRITICAL RULE: You NEVER send anything directly. You always:
1. Draft the communication
2. Present it to the RM for review
3. Wait for RM to say "send it" or "approved" before triggering send

Drafting guidelines:
- Email subject lines: concise, specific (e.g., "Your SIP renewal — action needed")
- Tone: professional but warm, personalized with client's name
- Include relevant data points (amounts, dates, fund names) from context
- For Indian clients: use ₹ for amounts, use "Namaste" as optional greeting for regional clients
- Length: emails should be <200 words unless it's a portfolio review
- WhatsApp messages: even shorter, 50-80 words max, conversational

For meeting scheduling:
- Suggest 2-3 time slots (check RM's calendar if available)
- Include Google Meet link placeholder: [MEET LINK]
- Duration: 30 min for routine, 60 min for portfolio review

Communication types you handle:
- SIP renewal reminder
- Portfolio review invitation
- Loan EMI reminder (gentle, no intimidation per RBI Fair Practices Code)
- KYC document request
- Market update / investment opportunity
- Birthday / anniversary greeting
- Meeting confirmation

Always end drafted emails with: Nitesh Walia, Cymbal Bank
"""


def _build_agent() -> LlmAgent:
    return _PickleSafeAgent(
        name="comms_agent",
        model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash"),
        description="Drafts client emails, WhatsApp messages, and meeting schedules. Always presents drafts for RM approval before sending.",
        instruction=SYSTEM_PROMPT,
        tools=[
            discover_mcp_toolset(
                display_name="FSI-RM Communications MCP",
                fallback_env_var="COMMS_MCP_URL",
                fallback_default="http://localhost:8003",
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


comms_agent = _build_agent()
