"""
Communications MCP Server
Handles email drafting, calendar scheduling, and WhatsApp message staging.
CRITICAL: All outbound actions are STAGED (status=pending_rm_approval).
Nothing sends until RM explicitly approves via approve_draft().

Run locally:  python mcp_servers/comms_mcp.py
Deploy:       gcloud run deploy comms-mcp --source .
"""

import os
import uuid
from datetime import datetime, date, timedelta
from google.cloud import bigquery
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

BQ_PROJECT = os.getenv("GCP_PROJECT", "your-project")
BQ_DATASET = os.getenv("BQ_DATASET", "fsi_rm_poc")

_no_dns_rebinding = TransportSecuritySettings(enable_dns_rebinding_protection=False)
mcp = FastMCP("comms-mcp", transport_security=_no_dns_rebinding)
bq = bigquery.Client(project=BQ_PROJECT)


def _run_query(sql: str) -> list[dict]:
    rows = bq.query(sql).result()
    return [dict(row) for row in rows]


@mcp.tool()
def create_email_draft(
    client_id: str,
    subject: str,
    body: str,
    to_email: str,
    rm_id: str,
    communication_type: str = "general",
) -> dict:
    """
    Stage an email draft for RM approval. Does NOT send.
    Returns draft_id for the RM to approve or discard.
    communication_type: sip_renewal | portfolio_review | kyc_reminder | emi_reminder | birthday | general
    """
    draft_id = str(uuid.uuid4())
    rows = [{
        "draft_id": draft_id,
        "client_id": client_id,
        "rm_id": rm_id,
        "channel": "email",
        "communication_type": communication_type,
        "to_address": to_email,
        "subject": subject,
        "body": body,
        "status": "pending_rm_approval",
        "created_at": datetime.utcnow().isoformat(),
        "approved_at": None,
        "sent_at": None,
    }]
    bq.insert_rows_json(f"{BQ_PROJECT}.{BQ_DATASET}.comms_drafts", rows)
    return {
        "draft_id": draft_id,
        "status": "pending_rm_approval",
        "message": f"Email draft created. Awaiting RM approval. Draft ID: {draft_id}",
        "preview": {"subject": subject, "to": to_email, "body_preview": body[:200]},
    }


@mcp.tool()
def create_whatsapp_draft(
    client_id: str,
    mobile: str,
    message: str,
    rm_id: str,
    communication_type: str = "general",
) -> dict:
    """
    Stage a WhatsApp message draft for RM approval. Does NOT send.
    Returns draft_id for RM to approve or discard.
    """
    draft_id = str(uuid.uuid4())
    rows = [{
        "draft_id": draft_id,
        "client_id": client_id,
        "rm_id": rm_id,
        "channel": "whatsapp",
        "communication_type": communication_type,
        "to_address": mobile,
        "subject": None,
        "body": message,
        "status": "pending_rm_approval",
        "created_at": datetime.utcnow().isoformat(),
        "approved_at": None,
        "sent_at": None,
    }]
    bq.insert_rows_json(f"{BQ_PROJECT}.{BQ_DATASET}.comms_drafts", rows)
    return {
        "draft_id": draft_id,
        "status": "pending_rm_approval",
        "message": f"WhatsApp draft created. Awaiting RM approval. Draft ID: {draft_id}",
        "preview": {"to": mobile, "message_preview": message[:150]},
    }


@mcp.tool()
def approve_draft(draft_id: str, rm_id: str) -> dict:
    """
    RM approves a staged draft. Marks it approved (triggers actual send in production).
    In PoC: marks status=approved in BigQuery (no actual send).
    """
    sql = f"""
        UPDATE `{BQ_PROJECT}.{BQ_DATASET}.comms_drafts`
        SET status = 'approved', approved_at = '{datetime.utcnow().isoformat()}'
        WHERE draft_id = '{draft_id}' AND rm_id = '{rm_id}'
    """
    bq.query(sql).result()
    return {
        "draft_id": draft_id,
        "status": "approved",
        "message": "Draft approved. In production this would trigger the actual send via Gmail API / WhatsApp Business API.",
    }


@mcp.tool()
def get_pending_drafts(rm_id: str) -> dict:
    """Get all pending drafts awaiting RM approval."""
    sql = f"""
        SELECT draft_id, client_id, channel, communication_type,
               to_address, subject, LEFT(body, 200) AS body_preview,
               created_at
        FROM `{BQ_PROJECT}.{BQ_DATASET}.comms_drafts`
        WHERE rm_id = '{rm_id}' AND status = 'pending_rm_approval'
        ORDER BY created_at DESC
    """
    return {"rm_id": rm_id, "pending_drafts": _run_query(sql)}


@mcp.tool()
def suggest_meeting_slots(rm_id: str, client_id: str, duration_minutes: int = 30) -> dict:
    """
    Suggest 3 available meeting slots for the next 5 business days.
    In PoC: returns mock slots. In production: checks Google Calendar API.
    """
    slots = []
    d = date.today() + timedelta(days=1)
    count = 0
    while count < 3:
        if d.weekday() < 5:  # weekday only
            for hour in [10, 14, 16]:
                if count < 3:
                    slots.append({
                        "date": d.isoformat(),
                        "time": f"{hour:02d}:00 IST",
                        "duration_minutes": duration_minutes,
                        "google_meet_link": f"https://meet.google.com/mock-{uuid.uuid4().hex[:8]}",
                    })
                    count += 1
        d += timedelta(days=1)
    return {
        "rm_id": rm_id,
        "client_id": client_id,
        "suggested_slots": slots,
        "note": "Share these slots with the client and confirm one. In production, selected slot will auto-create a Google Calendar event.",
    }


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8003))
    inner = mcp.streamable_http_app()

    uvicorn.run(inner, host="0.0.0.0", port=port)
