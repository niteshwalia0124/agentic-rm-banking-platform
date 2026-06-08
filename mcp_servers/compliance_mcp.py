"""
Compliance MCP Server
KYC expiry monitoring, AML alerts, loan covenant checks, regulatory reminders.
Aligned with RBI FREE-AI framework, DPDP Act 2023, RBI KYC Master Direction.

Run locally:  python mcp_servers/compliance_mcp.py
Deploy:       gcloud run deploy compliance-mcp --source .
"""

import os
from datetime import date
from google.cloud import bigquery
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

BQ_PROJECT = os.getenv("GCP_PROJECT", "your-project")
BQ_DATASET = os.getenv("BQ_DATASET", "fsi_rm_poc")

_no_dns_rebinding = TransportSecuritySettings(enable_dns_rebinding_protection=False)
mcp = FastMCP("compliance-mcp", transport_security=_no_dns_rebinding)
bq = bigquery.Client(project=BQ_PROJECT)


def _run_query(sql: str) -> list[dict]:
    rows = bq.query(sql).result()
    return [dict(row) for row in rows]


@mcp.tool()
def get_daily_compliance_digest(rm_id: str) -> dict:
    """
    Generate daily compliance digest for an RM.
    Returns urgent/this-week/this-month action items across all their clients.
    """
    # KYC expiring
    kyc_sql = f"""
        SELECT c.client_id, c.full_name, k.document_type, k.expiry_date,
               DATE_DIFF(k.expiry_date, CURRENT_DATE(), DAY) AS days_to_expiry,
               CASE
                 WHEN DATE_DIFF(k.expiry_date, CURRENT_DATE(), DAY) <= 7 THEN 'URGENT'
                 WHEN DATE_DIFF(k.expiry_date, CURRENT_DATE(), DAY) <= 30 THEN 'THIS_WEEK'
                 ELSE 'THIS_MONTH'
               END AS urgency
        FROM `{BQ_PROJECT}.{BQ_DATASET}.kyc_documents` k
        JOIN `{BQ_PROJECT}.{BQ_DATASET}.clients` c USING(client_id)
        WHERE c.rm_id = '{rm_id}'
          AND DATE_DIFF(k.expiry_date, CURRENT_DATE(), DAY) BETWEEN 0 AND 90
        ORDER BY days_to_expiry ASC
    """

    # Overdue EMIs
    emi_sql = f"""
        SELECT c.client_id, c.full_name, l.loan_type, l.outstanding_inr,
               l.emi_amount_inr, l.dpd_days,
               CASE
                 WHEN l.dpd_days >= 60 THEN 'URGENT'
                 WHEN l.dpd_days >= 30 THEN 'THIS_WEEK'
                 ELSE 'THIS_MONTH'
               END AS urgency
        FROM `{BQ_PROJECT}.{BQ_DATASET}.loans` l
        JOIN `{BQ_PROJECT}.{BQ_DATASET}.clients` c USING(client_id)
        WHERE c.rm_id = '{rm_id}' AND l.dpd_days > 0 AND l.status = 'active'
        ORDER BY l.dpd_days DESC
    """

    # Nomination not updated
    nomination_sql = f"""
        SELECT c.client_id, c.full_name, a.account_number, a.account_type
        FROM `{BQ_PROJECT}.{BQ_DATASET}.accounts` a
        JOIN `{BQ_PROJECT}.{BQ_DATASET}.clients` c USING(client_id)
        WHERE c.rm_id = '{rm_id}' AND a.nomination_updated = FALSE AND a.status = 'active'
        LIMIT 20
    """

    kyc_alerts = _run_query(kyc_sql)
    emi_alerts = _run_query(emi_sql)
    nomination_alerts = _run_query(nomination_sql)

    return {
        "rm_id": rm_id,
        "digest_date": date.today().isoformat(),
        "kyc_expiry_alerts": {
            "urgent": [a for a in kyc_alerts if a["urgency"] == "URGENT"],
            "this_week": [a for a in kyc_alerts if a["urgency"] == "THIS_WEEK"],
            "this_month": [a for a in kyc_alerts if a["urgency"] == "THIS_MONTH"],
        },
        "overdue_emi_alerts": {
            "urgent": [a for a in emi_alerts if a["urgency"] == "URGENT"],
            "this_week": [a for a in emi_alerts if a["urgency"] == "THIS_WEEK"],
            "this_month": [a for a in emi_alerts if a["urgency"] == "THIS_MONTH"],
        },
        "nomination_pending": nomination_alerts,
    }


@mcp.tool()
def get_clients_not_contacted(rm_id: str, days: int = 30) -> dict:
    """Get clients who haven't been contacted in N days — for proactive reach-out."""
    sql = f"""
        SELECT client_id, full_name, segment, total_aum_inr,
               last_contact_date,
               DATE_DIFF(CURRENT_DATE(), last_contact_date, DAY) AS days_since_contact
        FROM `{BQ_PROJECT}.{BQ_DATASET}.clients`
        WHERE rm_id = '{rm_id}'
          AND DATE_DIFF(CURRENT_DATE(), last_contact_date, DAY) >= {days}
        ORDER BY total_aum_inr DESC
    """
    clients = _run_query(sql)
    return {
        "rm_id": rm_id,
        "threshold_days": days,
        "stale_clients": clients,
        "count": len(clients),
    }


@mcp.tool()
def get_upcoming_birthdays_anniversaries(rm_id: str, days_ahead: int = 7) -> dict:
    """Get clients with birthdays or anniversaries in the next N days."""
    sql = f"""
        SELECT client_id, full_name, mobile, email,
               CASE
                 WHEN FORMAT_DATE('%m-%d', date_of_birth) = FORMAT_DATE('%m-%d', DATE_ADD(CURRENT_DATE(), INTERVAL seq DAY))
                 THEN 'Birthday'
                 ELSE 'Anniversary'
               END AS event_type,
               DATE_ADD(CURRENT_DATE(), INTERVAL seq DAY) AS event_date
        FROM `{BQ_PROJECT}.{BQ_DATASET}.clients`
        CROSS JOIN UNNEST(GENERATE_ARRAY(0, {days_ahead})) AS seq
        WHERE rm_id = '{rm_id}'
          AND (
            FORMAT_DATE('%m-%d', date_of_birth) = FORMAT_DATE('%m-%d', DATE_ADD(CURRENT_DATE(), INTERVAL seq DAY))
            OR FORMAT_DATE('%m-%d', anniversary_date) = FORMAT_DATE('%m-%d', DATE_ADD(CURRENT_DATE(), INTERVAL seq DAY))
          )
        ORDER BY event_date ASC
    """
    return {
        "rm_id": rm_id,
        "upcoming_events": _run_query(sql),
    }


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8004))
    inner = mcp.streamable_http_app()

    uvicorn.run(inner, host="0.0.0.0", port=port)
