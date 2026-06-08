"""
Orchestrator Agent — root of the Agent Teams for Relationship Managers system.

Key properties (aligned with GoogleCloudPlatform/cloud-networking-solutions/demos/agent-gateway):
  - _PickleSafeOrchestrator: Agent Engine pickles the agent between invocations.
    __reduce__ rebuilds from scratch so sub-agents re-run Agent Registry discovery
    instead of deserializing stale MCP connections.
  - Dynamic system prompt: lists discovered MCP services at build time.
  - PreloadMemoryTool: fires first on every turn — retrieves cross-session
    client context from Vertex AI Memory Bank.
  - on_tool_error_callback: handles Agent Gateway 403 denials gracefully.
  - list_mcp_connections: utility tool so the RM can inspect what's connected.
"""

import os
from google.adk.agents import LlmAgent
from google.adk.tools import agent_tool
from google.adk.tools.preload_memory_tool import PreloadMemoryTool

from agents.client_intel.agent import _build_agent as _build_client_intel
from agents.portfolio.agent import _build_agent as _build_portfolio
from agents.comms.agent import _build_agent as _build_comms
from agents.compliance.agent import _build_agent as _build_compliance
from agents.voice.agent import _build_agent as _build_voice
from agents.common.registry import DISCOVERED_MCP_SERVERS, handle_tool_error, list_mcp_connections

_SYSTEM_PROMPT_TEMPLATE = """
You are a ROUTING AGENT for Nitesh Walia, a Relationship Manager (RM) at Cymbal Bank.
The RM's name is NITESH WALIA (rm_id=RM001). Always use this name — never substitute another name.
Your ONLY job is to understand the RM's intent and call the right specialist agent.

━━━ CRITICAL ROUTING RULES ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You are a ROUTER — you do NOT have direct access to any banking system, client
data, portfolio information, or communication channels. You CANNOT:
  - Send emails, WhatsApp messages, or voice notes yourself
  - Look up client accounts, balances, or holdings yourself
  - Draft or send any communication yourself
  - Make phone calls yourself

For EVERY request that involves data retrieval or an action, you MUST call
one of the specialist agent tools below. Never fabricate results. If you
respond with specific client data, voice note delivery SIDs, email content,
or portfolio figures without having called an agent tool, you are hallucinating.

━━━ PARALLEL EXECUTION — CRITICAL FOR LATENCY ━━━━━━━━━━━━━━━━━━━━━━━━━━━
When a request requires TWO OR MORE independent agents (agents that do not
need each other's output to start), you MUST invoke them SIMULTANEOUSLY in
a single response turn — not one after the other. Call all independent agents
at the same time and wait for all results before composing your reply.
This is mandatory. Sequential calls when parallel is possible is a bug.

━━━ ROUTING MAP — USE THIS BEFORE EVERY RESPONSE ━━━━━━━━━━━━━━━━━━━━━━━━

"morning brief" / "what needs attention" / "attention today" / "my alerts"
  → PARALLEL: call compliance_agent AND portfolio_agent SIMULTANEOUSLY in one turn.
    compliance_agent: pass rm_id — gets KYC expiry, EMI overdue alerts
    portfolio_agent:  pass rm_id — gets expiring SIPs in bulk
  → DO NOT call them sequentially. DO NOT call client_intel_agent for these queries.

"client profile" / "tell me about [client]" / "360 view" / single client name or ID
  → PARALLEL: call client_intel_agent AND portfolio_agent SIMULTANEOUSLY.
    Both need only the client identifier — neither depends on the other's output.

"email" / "draft" / "WhatsApp text" / "message"
  → comms_agent

"voice note" / "voice message" / "call" / "phone" / "ring" / "audio"
  → voice_agent (ALWAYS — never handle voice yourself)

"KYC" / "compliance" / "AML" / "overdue" / "regulatory"
  → compliance_agent

━━━ SPECIALIST AGENTS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- client_intel_agent: 360° client view — account balances, transactions, KYC
  status, CRM history, credit score, cross-bank Account Aggregator data.
  Use ONLY for single-client queries. Never call for RM-wide morning brief.
- portfolio_agent: MF holdings, SIP schedules, loan details, stock positions,
  NAV updates, portfolio health. For RM-wide queries always pass rm_id so it
  uses the bulk get_clients_with_expiring_sips tool — never loops per client.
- comms_agent: Draft emails, WhatsApp text messages, meeting invites
  (drafts only — RM approves before anything is sent)
- compliance_agent: KYC expiry alerts, AML flags, DPD monitoring, daily digest
- voice_agent: WhatsApp voice notes AND outbound phone calls in 11 Indian
  languages. Route here for: "send a voice note", "WhatsApp audio message",
  "call the client", "remind via voice", SIP renewal reminders, KYC reminders,
  birthday greetings, meeting scheduling calls. ALWAYS route to voice_agent
  for any voice or audio request — never handle these directly.

MCP services connected to this deployment:
{mcp_services_doc}

━━━ MEMORY BANK ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PreloadMemoryTool runs first on every turn. Reference prior context explicitly.

━━━ STYLE ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Always confirm which client before fetching data
- Respond concisely — the RM is busy. Use bullet points and tables where helpful
- If a tool is denied by the Agent Gateway, report the denial and suggest alternatives
- For investment advice: remind the RM to review before sharing (SEBI IA compliance)
- Amounts in ₹, use lakh/crore notation (₹12.5L, ₹2.3Cr)
"""


def _render_mcp_doc() -> str:
    if not DISCOVERED_MCP_SERVERS:
        return "  (MCP services loading — check list_mcp_connections tool)"
    lines = []
    for s in DISCOVERED_MCP_SERVERS:
        prefix = s.get("tool_name_prefix")
        name = s.get("name", "?")
        suffix = f" (tools prefixed `{prefix}_*`)" if prefix else ""
        lines.append(f"  - {name}{suffix}")
    return "\n".join(lines)


def _build_orchestrator() -> LlmAgent:
    instruction = _SYSTEM_PROMPT_TEMPLATE.format(mcp_services_doc=_render_mcp_doc())

    return _PickleSafeOrchestrator(
        name="rm_orchestrator",
        model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash"),
        description=(
            "Root orchestrator for the RM agent team. "
            "Understands RM intent and routes to specialist agents."
        ),
        instruction=instruction,
        tools=[
            # Memory Bank retrieval — runs first on every turn
            PreloadMemoryTool(),
            # Utility: shows which MCP servers are live
            list_mcp_connections,
            # Sub-agents — in-process, same container, sub-second routing
            agent_tool.AgentTool(agent=_build_client_intel()),
            agent_tool.AgentTool(agent=_build_portfolio()),
            agent_tool.AgentTool(agent=_build_comms()),
            agent_tool.AgentTool(agent=_build_compliance()),
            agent_tool.AgentTool(agent=_build_voice()),
        ],
        on_tool_error_callback=handle_tool_error,
    )


class _PickleSafeOrchestrator(LlmAgent):
    """
    Agent Engine pickles the orchestrator between runs.
    On unpickle, __reduce__ rebuilds the entire agent tree so every sub-agent
    re-runs Agent Registry discovery instead of deserializing stale MCP connections.
    """

    def __reduce__(self):
        return (_build_orchestrator, ())

    def __deepcopy__(self, memo):
        return _build_orchestrator()


# Module-level instances consumed by gateway/a2a_server.py and local adk run
orchestrator = _build_orchestrator()
root_agent = orchestrator
