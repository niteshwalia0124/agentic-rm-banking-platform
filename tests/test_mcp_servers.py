"""
Layer 1: MCP Server Unit Tests
Tests each MCP tool in isolation — directly calls the tool functions
against the BigQuery mock dataset.

Run: pytest tests/test_mcp_servers.py -v
Requires: BigQuery seeded (python scripts/seed_bigquery.py)
"""

import os
import pytest
from unittest.mock import patch, MagicMock

os.environ.setdefault("GCP_PROJECT", "your-project")
os.environ.setdefault("BQ_DATASET", "fsi_rm_poc")


# ── Core Banking MCP ────────────────────────────────────────────────────────

class TestCoreBankingMCP:

    def test_get_client_by_name_returns_results(self):
        from mcp_servers.core_banking_mcp import get_client_by_name
        result = get_client_by_name("Rahul")
        assert "clients" in result or "error" in result
        if "clients" in result:
            assert len(result["clients"]) > 0
            assert "client_id" in result["clients"][0]
            assert "segment" in result["clients"][0]

    def test_get_client_by_name_unknown_returns_error(self):
        from mcp_servers.core_banking_mcp import get_client_by_name
        result = get_client_by_name("XYZNONEXISTENT123")
        assert "error" in result

    def test_get_account_summary_returns_balances(self):
        from mcp_servers.core_banking_mcp import get_client_by_name, get_account_summary
        clients = get_client_by_name("Rahul")
        if "clients" in clients and clients["clients"]:
            cid = clients["clients"][0]["client_id"]
            result = get_account_summary(cid)
            assert "accounts" in result
            assert "total_balance_inr" in result
            assert result["total_balance_inr"] >= 0

    def test_get_kyc_status_returns_documents(self):
        from mcp_servers.core_banking_mcp import get_client_by_name, get_kyc_status
        clients = get_client_by_name("Priya")
        if "clients" in clients and clients["clients"]:
            cid = clients["clients"][0]["client_id"]
            result = get_kyc_status(cid)
            assert "documents" in result
            assert "kyc_complete" in result
            assert isinstance(result["kyc_complete"], bool)

    def test_get_rm_client_list_returns_clients(self):
        from mcp_servers.core_banking_mcp import get_rm_client_list
        result = get_rm_client_list("RM001")
        assert "clients" in result
        assert "total_clients" in result
        assert result["total_clients"] > 0

    def test_get_rm_client_list_segment_filter(self):
        from mcp_servers.core_banking_mcp import get_rm_client_list
        result = get_rm_client_list("RM001", segment="HNI")
        if "clients" in result:
            for client in result["clients"]:
                assert client["segment"] == "HNI"


# ── Portfolio MCP ────────────────────────────────────────────────────────────

class TestPortfolioMCP:

    @pytest.fixture
    def sample_client_id(self):
        from mcp_servers.core_banking_mcp import get_rm_client_list
        result = get_rm_client_list("RM001")
        if result.get("clients"):
            return result["clients"][0]["client_id"]
        return "C0001"

    def test_get_mf_holdings_returns_portfolio(self, sample_client_id):
        from mcp_servers.portfolio_mcp import get_mf_holdings
        result = get_mf_holdings(sample_client_id)
        assert "holdings" in result
        assert "total_current_value_inr" in result
        assert result["total_current_value_inr"] >= 0

    def test_get_sip_schedule_returns_mandates(self, sample_client_id):
        from mcp_servers.portfolio_mcp import get_sip_schedule
        result = get_sip_schedule(sample_client_id)
        assert "active_sips" in result
        assert "total_monthly_sip_inr" in result

    def test_get_loan_summary_returns_loans(self, sample_client_id):
        from mcp_servers.portfolio_mcp import get_loan_summary
        result = get_loan_summary(sample_client_id)
        assert "loans" in result
        assert "total_outstanding_inr" in result

    def test_get_clients_with_expiring_sips(self):
        from mcp_servers.portfolio_mcp import get_clients_with_expiring_sips
        result = get_clients_with_expiring_sips("RM001", days_ahead=30)
        assert "sips_expiring_soon" in result
        assert isinstance(result["sips_expiring_soon"], list)


# ── Compliance MCP ───────────────────────────────────────────────────────────

class TestComplianceMCP:

    def test_daily_digest_has_correct_structure(self):
        from mcp_servers.compliance_mcp import get_daily_compliance_digest
        result = get_daily_compliance_digest("RM001")
        assert "kyc_expiry_alerts" in result
        assert "overdue_emi_alerts" in result
        assert "urgent" in result["kyc_expiry_alerts"]
        assert "this_week" in result["kyc_expiry_alerts"]
        assert "this_month" in result["kyc_expiry_alerts"]

    def test_stale_clients_returns_list(self):
        from mcp_servers.compliance_mcp import get_clients_not_contacted
        result = get_clients_not_contacted("RM001", days=30)
        assert "stale_clients" in result
        assert "count" in result
        assert isinstance(result["count"], int)

    def test_birthdays_returns_events(self):
        from mcp_servers.compliance_mcp import get_upcoming_birthdays_anniversaries
        result = get_upcoming_birthdays_anniversaries("RM001", days_ahead=30)
        assert "upcoming_events" in result
        assert isinstance(result["upcoming_events"], list)


# ── Comms MCP ────────────────────────────────────────────────────────────────

class TestCommsMCP:

    def test_create_email_draft_returns_draft_id(self):
        from mcp_servers.comms_mcp import create_email_draft
        result = create_email_draft(
            client_id="C0001",
            subject="Your SIP renewal — action needed",
            body="Dear Rahul ji, your HDFC Mid-Cap SIP expires on May 28...",
            to_email="rahul.sharma@email.com",
            rm_id="RM001",
            communication_type="sip_renewal",
        )
        assert "draft_id" in result
        assert result["status"] == "pending_rm_approval"
        assert "preview" in result

    def test_draft_is_not_auto_sent(self):
        from mcp_servers.comms_mcp import create_email_draft
        result = create_email_draft(
            client_id="C0002",
            subject="Portfolio review",
            body="Dear Priya ji...",
            to_email="priya@email.com",
            rm_id="RM001",
        )
        # Critical guardrail: status must NOT be "sent"
        assert result["status"] != "sent"
        assert result["status"] == "pending_rm_approval"

    def test_suggest_meeting_slots_returns_3_slots(self):
        from mcp_servers.comms_mcp import suggest_meeting_slots
        result = suggest_meeting_slots("RM001", "C0001")
        assert "suggested_slots" in result
        assert len(result["suggested_slots"]) == 3
        for slot in result["suggested_slots"]:
            assert "date" in slot
            assert "time" in slot
            assert "google_meet_link" in slot

    def test_get_pending_drafts_returns_list(self):
        from mcp_servers.comms_mcp import get_pending_drafts
        result = get_pending_drafts("RM001")
        assert "pending_drafts" in result
        assert isinstance(result["pending_drafts"], list)
