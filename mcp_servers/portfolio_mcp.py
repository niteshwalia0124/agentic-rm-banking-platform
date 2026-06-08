"""
Portfolio MCP Server
Exposes mutual fund holdings, SIP schedules, loan details, and stock positions.
In PoC: reads from BigQuery mock dataset + real AMFI/market data via AWS A2A agents.
In Production: bridges to CDSL/NSDL (demat), AMFI (MF NAV), Loan Origination System.

Run locally:  python mcp_servers/portfolio_mcp.py
Deploy:       gcloud run deploy portfolio-mcp --source .
"""

import asyncio
import os
from datetime import date, timedelta
from google.cloud import bigquery
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

# A2A agent URLs (set in .env after deploying to AWS Lambda)
AMFI_AGENT_URL = os.getenv("AMFI_AGENT_URL", "")
MARKET_DATA_AGENT_URL = os.getenv("MARKET_DATA_AGENT_URL", "")

BQ_PROJECT = os.getenv("GCP_PROJECT", "your-project")
BQ_DATASET = os.getenv("BQ_DATASET", "fsi_rm_poc")

_no_dns_rebinding = TransportSecuritySettings(enable_dns_rebinding_protection=False)
mcp = FastMCP("portfolio-mcp", transport_security=_no_dns_rebinding)
bq = bigquery.Client(project=BQ_PROJECT)


def _run_query(sql: str) -> list[dict]:
    rows = bq.query(sql).result()
    return [dict(row) for row in rows]


@mcp.tool()
def get_mf_holdings(client_id: str) -> dict:
    """Get mutual fund holdings for a client with current NAV and gain/loss."""
    sql = f"""
        SELECT fund_name, amc_name, scheme_type, units, purchase_nav,
               current_nav, current_value_inr, invested_amount_inr,
               ROUND((current_value_inr - invested_amount_inr) / invested_amount_inr * 100, 2) AS gain_pct,
               as_of_date
        FROM `{BQ_PROJECT}.{BQ_DATASET}.mf_holdings`
        WHERE client_id = '{client_id}'
        ORDER BY current_value_inr DESC
    """
    holdings = _run_query(sql)
    total_value = sum(h["current_value_inr"] for h in holdings)
    total_invested = sum(h["invested_amount_inr"] for h in holdings)
    return {
        "client_id": client_id,
        "holdings": holdings,
        "total_current_value_inr": total_value,
        "total_invested_inr": total_invested,
        "overall_gain_pct": round((total_value - total_invested) / total_invested * 100, 2) if total_invested else 0,
    }


@mcp.tool()
def get_sip_schedule(client_id: str) -> dict:
    """Get active SIP mandates with next debit date and expiry."""
    today = date.today().isoformat()
    sql = f"""
        SELECT sip_id, fund_name, monthly_amount_inr, next_debit_date,
               expiry_date, start_date, status,
               DATE_DIFF(expiry_date, CURRENT_DATE(), DAY) AS days_to_expiry
        FROM `{BQ_PROJECT}.{BQ_DATASET}.sip_mandates`
        WHERE client_id = '{client_id}' AND status = 'active'
        ORDER BY days_to_expiry ASC
    """
    sips = _run_query(sql)
    expiring_soon = [s for s in sips if s.get("days_to_expiry") is not None and s["days_to_expiry"] <= 30]
    return {
        "client_id": client_id,
        "active_sips": sips,
        "total_monthly_sip_inr": sum(s["monthly_amount_inr"] for s in sips),
        "expiring_in_30_days": expiring_soon,
    }


@mcp.tool()
def get_loan_summary(client_id: str) -> dict:
    """Get all active loans with outstanding balance, EMI, and overdue status."""
    sql = f"""
        SELECT loan_id, loan_type, sanctioned_amount_inr, outstanding_inr,
               emi_amount_inr, next_emi_date, dpd_days, ltv_ratio,
               interest_rate_pct, maturity_date, collateral_value_inr
        FROM `{BQ_PROJECT}.{BQ_DATASET}.loans`
        WHERE client_id = '{client_id}' AND status = 'active'
        ORDER BY outstanding_inr DESC
    """
    loans = _run_query(sql)
    overdue = [l for l in loans if l.get("dpd_days", 0) > 0]
    ltv_breach = [l for l in loans if l.get("ltv_ratio") and l["ltv_ratio"] > 80]
    return {
        "client_id": client_id,
        "loans": loans,
        "total_outstanding_inr": sum(l["outstanding_inr"] for l in loans),
        "total_emi_inr": sum(l["emi_amount_inr"] for l in loans),
        "overdue_loans": overdue,
        "ltv_breach_loans": ltv_breach,
    }


@mcp.tool()
def get_demat_holdings(client_id: str, top_n: int = 10) -> dict:
    """Get top N stock holdings from demat account."""
    sql = f"""
        SELECT isin, company_name, exchange, quantity, avg_buy_price,
               current_price, current_value_inr, unrealized_pnl_inr,
               ROUND(unrealized_pnl_inr / (quantity * avg_buy_price) * 100, 2) AS pnl_pct
        FROM `{BQ_PROJECT}.{BQ_DATASET}.demat_holdings`
        WHERE client_id = '{client_id}'
        ORDER BY current_value_inr DESC
        LIMIT {top_n}
    """
    stocks = _run_query(sql)
    return {
        "client_id": client_id,
        "top_holdings": stocks,
        "total_equity_value_inr": sum(s["current_value_inr"] for s in stocks),
    }


@mcp.tool()
def get_clients_with_expiring_sips(rm_id: str, days_ahead: int = 30) -> dict:
    """Get all clients of an RM whose SIPs expire within N days — for proactive outreach."""
    sql = f"""
        SELECT c.client_id, c.full_name, c.mobile, c.email,
               s.fund_name, s.monthly_amount_inr, s.expiry_date,
               DATE_DIFF(s.expiry_date, CURRENT_DATE(), DAY) AS days_to_expiry
        FROM `{BQ_PROJECT}.{BQ_DATASET}.sip_mandates` s
        JOIN `{BQ_PROJECT}.{BQ_DATASET}.clients` c USING(client_id)
        WHERE c.rm_id = '{rm_id}'
          AND s.status = 'active'
          AND DATE_DIFF(s.expiry_date, CURRENT_DATE(), DAY) BETWEEN 0 AND {days_ahead}
        ORDER BY days_to_expiry ASC
    """
    return {
        "rm_id": rm_id,
        "sips_expiring_soon": _run_query(sql),
    }


@mcp.tool()
def get_live_nav(fund_name: str) -> dict:
    """
    Get real-time NAV for a mutual fund from the AMFI A2A agent (AWS Lambda).
    Falls back gracefully if agent is not deployed.

    Calls: AMFI NAV Agent on AWS ap-south-1 via A2A Protocol.
    Data: live from api.mfapi.in (no cache — always fresh).
    """
    from external_agents.a2a_client import a2a_call
    result = asyncio.run(a2a_call(
        agent_url=AMFI_AGENT_URL,
        query=f"Get NAV for {fund_name}",
    ))
    if "error" in result and not AMFI_AGENT_URL:
        result["note"] = "Set AMFI_AGENT_URL in .env after deploying external_agents/amfi_agent to AWS Lambda"
    return result


@mcp.tool()
def get_live_stock_price(symbol_or_name: str) -> dict:
    """
    Get real-time NSE/BSE stock price from the Market Data A2A agent (AWS Lambda).
    Falls back gracefully if agent is not deployed.

    Calls: Market Data Agent on AWS ap-south-1 via A2A Protocol.
    Data: live from Yahoo Finance / NSE.
    """
    from external_agents.a2a_client import a2a_call
    result = asyncio.run(a2a_call(
        agent_url=MARKET_DATA_AGENT_URL,
        query=f"Stock price for {symbol_or_name}",
    ))
    if "error" in result and not MARKET_DATA_AGENT_URL:
        result["note"] = "Set MARKET_DATA_AGENT_URL in .env after deploying external_agents/market_data_agent to AWS Lambda"
    return result


@mcp.tool()
def get_market_indices() -> dict:
    """
    Get live Nifty 50, Sensex, and Nifty Bank levels from the Market Data A2A agent.
    Calls: Market Data Agent on AWS ap-south-1 via A2A Protocol.
    """
    from external_agents.a2a_client import a2a_call
    return asyncio.run(a2a_call(
        agent_url=MARKET_DATA_AGENT_URL,
        query="Get current index levels for Nifty 50, Sensex, Nifty Bank",
    ))


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8002))
    inner = mcp.streamable_http_app()

    uvicorn.run(inner, host="0.0.0.0", port=port)
