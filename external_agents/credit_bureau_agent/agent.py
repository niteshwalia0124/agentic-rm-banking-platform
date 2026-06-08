"""
Credit Bureau Agent — AWS Bedrock AgentCore (A2A protocol)

Runs on port 9000 as an A2A-compliant JSON-RPC server.
AgentCore Runtime acts as a transparent proxy — payloads pass through unchanged.

Mock CIBIL/Experian-format credit data. Realistic simulation.
In production: replace _mock_credit_report() with real CIBIL TransUnion API call.
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

AGENT_CARD = {
    "name": "Credit Bureau Agent",
    "description": "Mock CIBIL/Experian-format credit reports for Indian bank clients. Returns credit score, risk band, trade details, and DPD history. Swap for real CIBIL API in production.",
    "version": "1.0.0",
    "url": os.environ.get("AGENTCORE_RUNTIME_URL", ""),
    "protocolVersion": "0.3.0",
    "preferredTransport": "JSONRPC",
    "capabilities": {"streaming": False},
    "defaultInputModes": ["text"],
    "defaultOutputModes": ["text"],
    "skills": [
        {
            "id": "get_credit_report",
            "name": "Get Credit Report",
            "description": "Fetch CIBIL-format credit report for a client by ID (e.g. C0042) or PAN number",
            "tags": ["credit", "cibil", "experian", "score", "india"],
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
        pan_match = re.search(r"\b[A-Z]{5}\d{4}[A-Z]\b", text)
        client_id = client_match.group(0) if client_match else "UNKNOWN"
        pan = pan_match.group(0) if pan_match else ""

        result = _mock_credit_report(client_id, pan)

        return JSONResponse({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "artifacts": [{
                    "artifactId": str(uuid4()),
                    "name": "credit_report",
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


def _deterministic_score(seed: str) -> int:
    h = int(hashlib.md5(seed.encode()).hexdigest(), 16)
    return 650 + (h % 251)


def _risk_band(score: int) -> str:
    if score >= 800:   return "Excellent"
    elif score >= 750: return "Good"
    elif score >= 700: return "Fair"
    elif score >= 650: return "Average"
    return "Poor"


def _score_factors(score: int) -> list:
    if score >= 800:   return ["Excellent repayment history", "Low credit utilisation", "Long credit history"]
    elif score >= 750: return ["Good repayment history", "Moderate credit utilisation"]
    elif score >= 700: return ["Some delayed payments in last 2 years", "High utilisation on credit card"]
    return ["Multiple DPD instances", "High debt-to-income ratio", "Recent enquiries"]


def _mock_credit_report(client_id: str, pan: str = "") -> dict:
    seed = pan if pan else client_id
    score = _deterministic_score(seed)
    h = int(hashlib.md5(seed.encode()).hexdigest(), 16)

    num_trades = 2 + (h % 5)
    num_enquiries_6m = h % 4
    dpd_30_count = 0 if score > 750 else (h % 2)
    dpd_90_count = 0 if score > 700 else (h % 1)

    loan_types = ["HomeLoan", "PersonalLoan", "AutoLoan", "CreditCard", "GoldLoan"]
    trades = []
    for i in range(num_trades):
        loan_seed = h + i
        outstanding = (loan_seed % 4000000) + 100000
        trades.append({
            "lender": ["HDFC Bank", "ICICI Bank", "SBI", "Axis Bank", "Bajaj Finance"][i % 5],
            "account_type": loan_types[i % len(loan_types)],
            "sanctioned_amount": outstanding + (loan_seed % 2000000),
            "outstanding_balance": outstanding,
            "emi": round(outstanding * 0.02, 0),
            "dpd_30_days": dpd_30_count if i == 0 else 0,
            "dpd_90_days": dpd_90_count if i == 0 else 0,
            "account_status": "Active",
            "opened_date": (date.today() - timedelta(days=365 * (1 + i % 4))).isoformat(),
        })

    return {
        "source": "Credit Bureau (mock CIBIL format)",
        "note": "PoC simulation. In production: real CIBIL TransUnion API.",
        "client_id": client_id,
        "pan": pan or f"XXXXX{client_id[-4:]}X",
        "report_date": date.today().isoformat(),
        "credit_score": score,
        "risk_band": _risk_band(score),
        "score_factors": _score_factors(score),
        "active_trades": num_trades,
        "total_outstanding_inr": sum(t["outstanding_balance"] for t in trades),
        "enquiries_last_6m": num_enquiries_6m,
        "dpd_30_day_instances": dpd_30_count,
        "dpd_90_day_instances": dpd_90_count,
        "trades": trades,
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9000)
