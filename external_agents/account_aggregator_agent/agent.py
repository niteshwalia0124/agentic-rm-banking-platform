"""
Account Aggregator Agent — AWS Bedrock AgentCore (A2A protocol)

Runs on port 9000 as an A2A-compliant JSON-RPC server.
AgentCore Runtime acts as a transparent proxy — payloads pass through unchanged.

Mock RBI AA framework data — cross-bank financial profile.
In production: client provides AA consent token; licensed AA (Finvu/OneMoney) fetches live data.
Deploy: see ../aws_deploy.sh
"""

import hashlib
import json
import os
import re
from datetime import date, timedelta
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()

BANKS = ["HDFC Bank", "ICICI Bank", "SBI", "Axis Bank", "Kotak Mahindra Bank",
         "IndusInd Bank", "Yes Bank", "Bank of Baroda", "Punjab National Bank"]
AMCs = ["Mirae Asset", "SBI Funds", "Axis AMC", "ICICI Pru AMC", "Nippon India", "DSP AMC"]

AGENT_CARD = {
    "name": "Account Aggregator Agent",
    "description": "Mock RBI Account Aggregator (AA) framework data showing a client's financial holdings across ALL banks — savings accounts, FDs, SIPs, insurance. In production: connects to licensed AA (Finvu, OneMoney, Perfios) with client consent.",
    "version": "1.0.0",
    "url": os.environ.get("AGENTCORE_RUNTIME_URL", ""),
    "protocolVersion": "0.3.0",
    "preferredTransport": "JSONRPC",
    "capabilities": {"streaming": False},
    "defaultInputModes": ["text"],
    "defaultOutputModes": ["text"],
    "skills": [
        {
            "id": "get_aa_profile",
            "name": "Get Cross-Bank Financial Profile",
            "description": "Fetch consolidated financial profile for a client across all banks via RBI AA framework. Query by client ID (e.g. C0042).",
            "tags": ["account-aggregator", "rbi-aa", "cross-bank", "india", "fintech"],
        },
    ],
}


@app.get("/ping")
def ping():
    from datetime import datetime
    return {"status": "Healthy", "time_of_last_update": int(datetime.utcnow().timestamp())}


@app.get("/.well-known/agent-card.json")
def agent_card():
    return JSONResponse(AGENT_CARD)


@app.post("/")
async def handle_jsonrpc(request: Request):
    body = await request.json()
    request_id = body.get("id")

    try:
        method = body.get("method", "")
        if method not in ("message/send", "tasks/send"):
            return _error(request_id, -32601, f"Method not found: {method}")

        params = body.get("params", {})
        message = params.get("message", {})
        text = _extract_text(message).upper()

        client_match = re.search(r"\bC\d{4}\b", text)
        client_id = client_match.group(0) if client_match else "C0001"
        result = _mock_aa_data(client_id)

        return JSONResponse({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "artifacts": [{
                    "artifactId": str(uuid4()),
                    "name": "aa_profile",
                    "parts": [{"kind": "text", "text": json.dumps(result, indent=2)}],
                }]
            },
        })
    except Exception as e:
        return _error(request_id, -32603, f"Internal error: {e}")


def _extract_text(message: dict) -> str:
    for part in message.get("parts", []):
        if part.get("kind") == "text":
            return part.get("text", "")
    return message.get("text", "")


def _error(request_id, code: int, message: str):
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def _mock_aa_data(client_id: str) -> dict:
    h = int(hashlib.md5(client_id.encode()).hexdigest(), 16)

    other_banks = [(BANKS[(h + i) % len(BANKS)], (h * (i + 2)) % 5000000 + 50000) for i in range(2 + h % 2)]

    savings_accounts = [
        {
            "fip": bank,
            "account_type": "Savings",
            "masked_account": f"XXXX{(h + i * 1000) % 10000:04d}",
            "balance_inr": bal,
            "last_updated": date.today().isoformat(),
        }
        for i, (bank, bal) in enumerate(other_banks)
    ]

    fds = []
    if h % 3 != 0:
        fds.append({
            "fip": BANKS[(h + 5) % len(BANKS)],
            "product": "Fixed Deposit",
            "principal_inr": ((h % 10) + 1) * 500000,
            "rate_pct": 6.5 + (h % 20) / 10,
            "maturity_date": (date.today() + timedelta(days=180 + h % 365)).isoformat(),
        })

    external_sips = [
        {
            "fip": AMCs[(h + i) % len(AMCs)],
            "fund_name": f"{AMCs[(h+i)%len(AMCs)]} {'Small Cap' if i % 2 else 'Flexi Cap'} Fund",
            "monthly_amount_inr": ((h + i * 500) % 20) * 1000 + 5000,
            "current_value_inr": ((h + i * 500) % 20) * 1000 * (12 + h % 24),
            "status": "active",
        }
        for i in range(1 + h % 2)
    ]

    insurance = []
    if h % 2 == 0:
        insurance.append({
            "fip": ["LIC", "HDFC Life", "ICICI Prudential Life"][h % 3],
            "policy_type": "Term Life",
            "sum_assured_inr": ((h % 5) + 1) * 10000000,
            "annual_premium_inr": ((h % 10) + 1) * 20000,
            "status": "active",
        })

    total_assets = (
        sum(a["balance_inr"] for a in savings_accounts)
        + sum(f["principal_inr"] for f in fds)
        + sum(s["current_value_inr"] for s in external_sips)
    )

    return {
        "source": "Account Aggregator (mock RBI AA format)",
        "note": "PoC simulation. In production: client provides AA consent token; licensed AA fetches live data.",
        "client_id": client_id,
        "consent_status": "active",
        "data_as_of": date.today().isoformat(),
        "fip_count": len({a["fip"] for a in savings_accounts} | {f["fip"] for f in fds}),
        "summary": {
            "total_cross_bank_assets_inr": round(total_assets),
            "external_savings_accounts": len(savings_accounts),
            "external_fds": len(fds),
            "external_sips": len(external_sips),
            "insurance_policies": len(insurance),
        },
        "savings_accounts": savings_accounts,
        "fixed_deposits": fds,
        "external_sips": external_sips,
        "insurance": insurance,
        "insight": (
            f"Client holds ₹{round(total_assets/100000, 1)}L in assets outside your bank. "
            "Consider consolidation opportunities."
        ),
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9000)
