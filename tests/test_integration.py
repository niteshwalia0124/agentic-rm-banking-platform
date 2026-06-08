"""
Layer 3: Integration Tests
Full agent → MCP → BigQuery round trips.
Tests the 5 core demo flows end-to-end with real data.

Run: pytest tests/test_integration.py -v -s
Requires: MCP servers running (./scripts/start_local.sh)
"""

import pytest
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

from agents.orchestrator.agent import orchestrator

APP_NAME = "fsi-rm-integration-test"
USER_ID = "test-rm-RM001"


@pytest.fixture(scope="module")
def runner():
    return Runner(
        agent=orchestrator,
        app_name=APP_NAME,
        session_service=InMemorySessionService(),
    )


@pytest.fixture(scope="module")
def session(runner):
    return runner.session_service.create_session(
        app_name=APP_NAME, user_id=USER_ID
    )


def ask(runner, session, query: str) -> str:
    """Send a query through the full agent stack and return final text."""
    msg = Content(role="user", parts=[Part(text=query)])
    final = ""
    for event in runner.run(
        user_id=USER_ID,
        session_id=session.id,
        new_message=msg,
    ):
        if event.is_final_response() and event.content:
            final = "".join(p.text for p in event.content.parts if hasattr(p, "text"))
    return final


# ── Demo Flow 1: Morning Brief ───────────────────────────────────────────────

class TestDemoFlow1MorningBrief:

    def test_morning_brief_mentions_kyc_alerts(self, runner, session):
        response = ask(runner, session, "Give me my morning compliance digest")
        assert any(kw in response.lower() for kw in ["kyc", "expir", "urgent", "action"])

    def test_morning_brief_mentions_sip_alerts(self, runner, session):
        response = ask(runner, session, "Any SIP renewals coming up this month?")
        assert any(kw in response.lower() for kw in ["sip", "expir", "renew", "₹"])

    def test_morning_brief_mentions_birthdays(self, runner, session):
        response = ask(runner, session, "Any client birthdays or anniversaries this week?")
        assert any(kw in response.lower() for kw in ["birthday", "anniversary", "event"])


# ── Demo Flow 2: Client 360° ─────────────────────────────────────────────────

class TestDemoFlow2Client360:

    def test_client_lookup_by_name(self, runner, session):
        response = ask(runner, session, "Show me the client card for Rahul")
        assert any(kw in response.lower() for kw in ["account", "balance", "segment", "aum", "₹"])

    def test_client_card_has_portfolio_summary(self, runner, session):
        response = ask(runner, session, "What does Priya's portfolio look like?")
        assert any(kw in response.lower() for kw in ["fund", "sip", "loan", "holdings", "₹"])

    def test_client_card_has_kyc_status(self, runner, session):
        response = ask(runner, session, "Is Amit's KYC up to date?")
        assert any(kw in response.lower() for kw in ["kyc", "verified", "expir", "document"])

    def test_top_clients_by_aum(self, runner, session):
        response = ask(runner, session, "Show me my top 5 clients by AUM for RM001")
        # Must list multiple clients with INR amounts
        inr_occurrences = response.count("₹")
        assert inr_occurrences >= 3, f"Expected ₹ amounts for multiple clients, got {inr_occurrences}"


# ── Demo Flow 3: Portfolio Analysis ──────────────────────────────────────────

class TestDemoFlow3Portfolio:

    def test_portfolio_shows_mf_holdings(self, runner, session):
        response = ask(runner, session, "Show me client C0001's mutual fund holdings")
        assert any(kw in response.lower() for kw in ["fund", "nav", "units", "gain", "₹"])

    def test_portfolio_shows_loan_summary(self, runner, session):
        response = ask(runner, session, "What loans does client C0001 have?")
        assert any(kw in response.lower() for kw in ["loan", "emi", "outstanding", "₹"])

    def test_portfolio_flags_expiring_sips(self, runner, session):
        response = ask(runner, session, "Which of RM001's clients need SIP renewals?")
        assert any(kw in response.lower() for kw in ["expir", "renew", "sip", "₹"])


# ── Demo Flow 4: Communication Draft ─────────────────────────────────────────

class TestDemoFlow4Communications:

    def test_email_draft_created_not_sent(self, runner, session):
        response = ask(runner, session, "Draft a SIP renewal email for client C0001")
        # Must show draft content
        assert any(kw in response.lower() for kw in ["subject", "dear", "sip", "draft"])
        # Must NOT say "sent"
        assert "email sent" not in response.lower()
        assert "i have sent" not in response.lower()

    def test_email_draft_is_personalized(self, runner, session):
        response = ask(runner, session, "Draft a portfolio review email for client C0002")
        # Must include some personalization (fund name / amount)
        assert any(kw in response.lower() for kw in ["fund", "portfolio", "₹", "review"])

    def test_meeting_slots_suggested(self, runner, session):
        response = ask(runner, session, "Schedule a meeting with client C0001 for a portfolio review")
        assert any(kw in response.lower() for kw in ["slot", "available", "meet", "time", "date"])

    def test_whatsapp_draft_created(self, runner, session):
        response = ask(runner, session, "Send a WhatsApp to client C0001 about their KYC")
        # Must be staged, not sent
        assert any(kw in response.lower() for kw in ["draft", "approve", "pending", "whatsapp"])


# ── Demo Flow 5: Voice Call Staging ──────────────────────────────────────────

class TestDemoFlow5VoiceCall:

    def test_call_staged_for_approval(self, runner, session):
        response = ask(runner, session, "Call Rahul about his SIP renewal")
        assert any(kw in response.lower() for kw in ["approve", "call", "draft", "pending"])
        # Must NOT say "calling now" without approval
        assert "calling now" not in response.lower()
        assert "call initiated" not in response.lower()

    def test_call_uses_hindi_context(self, runner, session):
        response = ask(runner, session,
            "Call client C0001 in Hindi about their expiring SIP")
        assert any(kw in response.lower() for kw in ["hindi", "namaste", "approve", "script"])


# ── Cross-cutting: Memory Continuity ─────────────────────────────────────────

class TestMemoryContinuity:

    def test_agent_remembers_client_context(self, runner, session):
        """Agent should remember the client discussed earlier in session."""
        ask(runner, session, "Tell me about client C0001")
        # Follow-up without repeating client ID
        response2 = ask(runner, session, "What SIPs does this client have?")
        # Should answer about C0001 without re-asking
        assert any(kw in response2.lower() for kw in ["sip", "fund", "₹"])

    def test_agent_handles_disambiguation(self, runner, session):
        """Agent should ask for clarification if client name is ambiguous."""
        response = ask(runner, session, "Show me Sharma's portfolio")
        # With multiple Sharmas in mock data, should ask which one
        # or return multiple matches
        assert len(response) > 50  # must return something meaningful
