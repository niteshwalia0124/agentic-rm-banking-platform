"""
Core Banking MCP Server
Exposes bank account, transaction, KYC, and CRM data to agents via MCP protocol.
Also proxies to external A2A agents on AWS for credit bureau and Account Aggregator data.
In PoC: reads from BigQuery mock dataset.
In Production: bridges to CBS (Finacle/BaNCS) via Private Service Connect.

Run locally:  python mcp_servers/core_banking_mcp.py
Deploy:       gcloud run deploy core-banking-mcp --source .
"""

import asyncio
import os
import json
from datetime import date, timedelta
from google.cloud import bigquery
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

# A2A agent URLs (set in .env after deploying to AWS Lambda)
CREDIT_BUREAU_AGENT_URL = os.getenv("CREDIT_BUREAU_AGENT_URL", "")
ACCOUNT_AGGREGATOR_AGENT_URL = os.getenv("ACCOUNT_AGGREGATOR_AGENT_URL", "")

BQ_PROJECT = os.getenv("GCP_PROJECT", "your-project")
BQ_DATASET = os.getenv("BQ_DATASET", "fsi_rm_poc")

_no_dns_rebinding = TransportSecuritySettings(enable_dns_rebinding_protection=False)
mcp = FastMCP("core-banking-mcp", transport_security=_no_dns_rebinding)
bq = bigquery.Client(project=BQ_PROJECT)


def _run_query(sql: str) -> list[dict]:
    rows = bq.query(sql).result()
    return [dict(row) for row in rows]


@mcp.tool()
def get_client_by_name(name: str) -> dict:
    """Find a client by name. Returns client ID, segment, and basic profile."""
    sql = f"""
        SELECT client_id, full_name, segment, risk_profile, rm_id, mobile, email,
               date_of_birth, anniversary_date, city, relationship_since
        FROM `{BQ_PROJECT}.{BQ_DATASET}.clients`
        WHERE LOWER(full_name) LIKE LOWER('%{name}%')
        LIMIT 5
    """
    results = _run_query(sql)
    if not results:
        return {"error": f"No client found matching '{name}'"}
    return {"clients": results}


@mcp.tool()
def get_account_summary(client_id: str) -> dict:
    """Get all account balances for a client (savings, current, FD, RD)."""
    sql = f"""
        SELECT account_number, account_type, balance_inr, currency,
               last_transaction_date, status
        FROM `{BQ_PROJECT}.{BQ_DATASET}.accounts`
        WHERE client_id = '{client_id}'
        ORDER BY balance_inr DESC
    """
    accounts = _run_query(sql)
    total = sum(a["balance_inr"] for a in accounts if a["status"] == "active")
    return {"client_id": client_id, "accounts": accounts, "total_balance_inr": total}


@mcp.tool()
def get_recent_transactions(client_id: str, days: int = 30, min_amount_inr: int = 100000) -> dict:
    """Get significant recent transactions for a client."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    sql = f"""
        SELECT txn_date, txn_type, amount_inr, description, channel, account_number
        FROM `{BQ_PROJECT}.{BQ_DATASET}.transactions`
        WHERE client_id = '{client_id}'
          AND txn_date >= '{cutoff}'
          AND ABS(amount_inr) >= {min_amount_inr}
        ORDER BY txn_date DESC
        LIMIT 20
    """
    return {"client_id": client_id, "transactions": _run_query(sql)}


@mcp.tool()
def get_kyc_status(client_id: str) -> dict:
    """Get KYC document status and expiry dates for a client."""
    sql = f"""
        SELECT document_type, status, expiry_date, last_updated,
               DATE_DIFF(expiry_date, CURRENT_DATE(), DAY) AS days_to_expiry
        FROM `{BQ_PROJECT}.{BQ_DATASET}.kyc_documents`
        WHERE client_id = '{client_id}'
        ORDER BY days_to_expiry ASC
    """
    docs = _run_query(sql)
    expiring_soon = [d for d in docs if d.get("days_to_expiry") is not None and d["days_to_expiry"] < 90]
    return {
        "client_id": client_id,
        "documents": docs,
        "expiring_soon": expiring_soon,
        "kyc_complete": all(d["status"] == "verified" for d in docs),
    }


@mcp.tool()
def get_crm_history(client_id: str, limit: int = 5) -> dict:
    """Get last N CRM interaction records for a client."""
    sql = f"""
        SELECT interaction_date, interaction_type, channel, summary, rm_name, outcome
        FROM `{BQ_PROJECT}.{BQ_DATASET}.crm_interactions`
        WHERE client_id = '{client_id}'
        ORDER BY interaction_date DESC
        LIMIT {limit}
    """
    interactions = _run_query(sql)
    last_contact = interactions[0]["interaction_date"] if interactions else None
    return {
        "client_id": client_id,
        "last_contact_date": str(last_contact) if last_contact else "Never",
        "interactions": interactions,
    }


@mcp.tool()
def get_rm_client_list(rm_id: str, segment: str = None) -> dict:
    """Get all clients assigned to an RM, optionally filtered by segment."""
    segment_filter = f"AND segment = '{segment}'" if segment else ""
    sql = f"""
        SELECT client_id, full_name, segment, city, total_aum_inr,
               last_contact_date,
               DATE_DIFF(CURRENT_DATE(), last_contact_date, DAY) AS days_since_contact
        FROM `{BQ_PROJECT}.{BQ_DATASET}.clients`
        WHERE rm_id = '{rm_id}' {segment_filter}
        ORDER BY total_aum_inr DESC
    """
    clients = _run_query(sql)
    return {
        "rm_id": rm_id,
        "total_clients": len(clients),
        "clients": clients,
    }


@mcp.tool()
def get_credit_bureau_report(client_id: str, pan: str = "") -> dict:
    """
    Get credit bureau report (CIBIL-format) for a client via Credit Bureau A2A agent (AWS Lambda).
    Returns: credit score, risk band, active trades, DPD history, total outstanding.

    Calls: Credit Bureau Agent on AWS ap-south-1 via A2A Protocol.
    PoC: realistic mock CIBIL format. Production: real CIBIL TransUnion API.
    """
    from external_agents.a2a_client import a2a_call
    query = f"Credit report for client {client_id}"
    if pan:
        query += f" PAN {pan}"
    result = asyncio.run(a2a_call(
        agent_url=CREDIT_BUREAU_AGENT_URL,
        query=query,
    ))
    if "error" in result and not CREDIT_BUREAU_AGENT_URL:
        result["note"] = "Set CREDIT_BUREAU_AGENT_URL in .env after deploying external_agents/credit_bureau_agent to AWS Lambda"
    return result


@mcp.tool()
def get_account_aggregator_data(client_id: str) -> dict:
    """
    Get client's complete financial picture across all banks via RBI Account Aggregator framework.
    Shows external FDs, SIPs, savings accounts, and insurance not visible in your CBS.

    Calls: Account Aggregator Agent on AWS ap-south-1 via A2A Protocol.
    PoC: realistic mock AA format. Production: requires client consent token from licensed AA.
    India's AA framework covers 2.2 billion accounts across 200+ institutions.
    """
    from external_agents.a2a_client import a2a_call
    result = asyncio.run(a2a_call(
        agent_url=ACCOUNT_AGGREGATOR_AGENT_URL,
        query=f"Full financial profile for client {client_id}",
    ))
    if "error" in result and not ACCOUNT_AGGREGATOR_AGENT_URL:
        result["note"] = "Set ACCOUNT_AGGREGATOR_AGENT_URL in .env after deploying external_agents/account_aggregator_agent to AWS Lambda"
    return result


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8001))
    inner = mcp.streamable_http_app()

    # FastMCP's TrustedHostMiddleware only allows localhost by default.
    # Cloud Run sends requests with its own domain as Host — rewrite to localhost
    # before the middleware sees it so all deployments are accepted.
    uvicorn.run(inner, host="0.0.0.0", port=port)
