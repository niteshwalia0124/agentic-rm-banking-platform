"""
AMFI NAV Agent — AWS Bedrock AgentCore (A2A protocol)

Runs on port 9000 as an A2A-compliant JSON-RPC server.
AgentCore Runtime acts as a transparent proxy — payloads pass through unchanged.

Data source: https://api.mfapi.in  (free, no auth required)
Deploy:      see ../aws_deploy.sh
"""

import json
import os
import re
from datetime import datetime
from uuid import uuid4

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()

AMFI_API_BASE = "https://api.mfapi.in/mf"

SCHEME_CODES = {
    "hdfc mid-cap opportunities": "119598",
    "sbi bluechip fund":          "125494",
    "mirae asset large cap fund": "118834",
    "axis liquid fund":           "120503",
    "icici pru balanced advantage": "130503",
    "kotak gilt fund":            "120481",
    "nippon india small cap fund":"118778",
    "parag parikh flexi cap fund":"122639",
}

AGENT_CARD = {
    "name": "AMFI NAV Agent",
    "description": "Real-time mutual fund NAV data from AMFI public API. Returns current NAV, 1Y/3Y/5Y CAGR returns, and fund search for Indian mutual funds.",
    "version": "1.0.0",
    "url": os.environ.get("AGENTCORE_RUNTIME_URL", ""),
    "protocolVersion": "0.3.0",
    "preferredTransport": "JSONRPC",
    "capabilities": {"streaming": False},
    "defaultInputModes": ["text"],
    "defaultOutputModes": ["text"],
    "skills": [
        {
            "id": "get_nav",
            "name": "Get Mutual Fund NAV",
            "description": "Fetch current NAV and historical returns for a mutual fund by name or scheme code",
            "tags": ["mutual-fund", "nav", "amfi", "india"],
        },
        {
            "id": "search_funds",
            "name": "Search Mutual Funds",
            "description": "Search for mutual funds by name or keyword",
            "tags": ["mutual-fund", "search", "amfi"],
        },
    ],
}


@app.get("/ping")
def ping():
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
        text = _extract_text(message)
        result = await _dispatch(text.lower())

        return JSONResponse({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "artifacts": [{
                    "artifactId": str(uuid4()),
                    "name": "nav_data",
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


async def _dispatch(query: str) -> dict:
    for fund_name, scheme_code in SCHEME_CODES.items():
        if fund_name in query or scheme_code in query:
            return await _get_nav(scheme_code, fund_name)

    code_match = re.search(r"\b(\d{5,6})\b", query)
    if code_match:
        return await _get_nav(code_match.group(1))

    return await _search_funds(query)


async def _get_nav(scheme_code: str, known_name: str = "") -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{AMFI_API_BASE}/{scheme_code}")
        if resp.status_code != 200:
            return {"error": f"AMFI API returned {resp.status_code} for scheme {scheme_code}"}

        data = resp.json()
        meta = data.get("meta", {})
        nav_data = data.get("data", [])

        if not nav_data:
            return {"error": "No NAV data returned"}

        latest = nav_data[0]
        current_nav = float(latest["nav"])

        returns = {}
        for days, label in [(365, "1y"), (1095, "3y"), (1825, "5y")]:
            if len(nav_data) > days:
                past_nav = float(nav_data[days]["nav"])
                if label == "1y":
                    ret = ((current_nav - past_nav) / past_nav) * 100
                else:
                    years = days / 365
                    ret = ((current_nav / past_nav) ** (1 / years) - 1) * 100
                returns[label] = round(ret, 2)

        return {
            "source": "AMFI (live)",
            "scheme_code": scheme_code,
            "fund_name": meta.get("scheme_name", known_name),
            "amc": meta.get("fund_house", ""),
            "scheme_type": meta.get("scheme_type", ""),
            "current_nav": current_nav,
            "nav_date": latest["date"],
            "returns_pct": returns,
            "fetched_at": datetime.utcnow().isoformat(),
        }


async def _search_funds(query: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{AMFI_API_BASE}/search?q={query}")
        if resp.status_code == 200:
            return {"source": "AMFI (live)", "query": query, "results": resp.json()[:10]}
        return {
            "source": "AMFI (cached)",
            "query": query,
            "known_schemes": list(SCHEME_CODES.items()),
        }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9000)
