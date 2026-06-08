"""
Market Data Agent — AWS Bedrock AgentCore (A2A protocol)

Runs on port 9000 as an A2A-compliant JSON-RPC server.
AgentCore Runtime acts as a transparent proxy — payloads pass through unchanged.

Data source: Yahoo Finance / yfinance (free, no auth)
Covers: NSE equities (.NS suffix), BSE equities (.BO suffix), indices (^NSEI, ^BSESN)
Deploy: see ../aws_deploy.sh
"""

import json
import os
import re
from datetime import datetime
from uuid import uuid4

import uvicorn
import yfinance as yf
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()

NSE_SYMBOLS = {
    "reliance": "RELIANCE.NS", "reliance industries": "RELIANCE.NS",
    "hdfc bank": "HDFCBANK.NS", "hdfcbank": "HDFCBANK.NS",
    "infosys": "INFY.NS",      "infy": "INFY.NS",
    "tcs": "TCS.NS",           "tata consultancy": "TCS.NS",
    "icici bank": "ICICIBANK.NS", "wipro": "WIPRO.NS",
    "kotak": "KOTAKBANK.NS",   "kotak mahindra": "KOTAKBANK.NS",
    "bajaj finance": "BAJFINANCE.NS",
    "sbi": "SBIN.NS",          "state bank": "SBIN.NS",
    "axis bank": "AXISBANK.NS",
    "l&t": "LT.NS",            "larsen": "LT.NS",
    "sun pharma": "SUNPHARMA.NS",
    "itc": "ITC.NS",
    "nifty 50": "^NSEI",
    "sensex": "^BSESN",
    "nifty bank": "^NSEBANK",
}

AGENT_CARD = {
    "name": "Market Data Agent",
    "description": "Real-time NSE/BSE stock prices and Indian market indices via Yahoo Finance. Covers Nifty 50, Sensex, and top Indian equities.",
    "version": "1.0.0",
    "url": os.environ.get("AGENTCORE_RUNTIME_URL", ""),
    "protocolVersion": "0.3.0",
    "preferredTransport": "JSONRPC",
    "capabilities": {"streaming": False},
    "defaultInputModes": ["text"],
    "defaultOutputModes": ["text"],
    "skills": [
        {
            "id": "get_stock_quote",
            "name": "Get Stock Quote",
            "description": "Fetch current price, day change, 52-week range and market cap for NSE/BSE equities",
            "tags": ["stocks", "nse", "bse", "equity", "india"],
        },
        {
            "id": "get_indices",
            "name": "Get Market Indices",
            "description": "Fetch current levels for Nifty 50, Sensex, and Nifty Bank",
            "tags": ["index", "nifty", "sensex", "market"],
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
        result = _dispatch(text.lower())

        return JSONResponse({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "artifacts": [{
                    "artifactId": str(uuid4()),
                    "name": "market_data",
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


def _dispatch(query: str) -> dict:
    if "index" in query or "nifty" in query or "sensex" in query:
        return _get_indices()

    for name, symbol in NSE_SYMBOLS.items():
        if name in query or symbol.lower().replace(".ns", "") in query:
            return _get_quote(symbol)

    ticker_match = re.search(r"\b([A-Z]{2,10}(?:\.NS|\.BO)?)\b", query.upper())
    if ticker_match:
        ticker = ticker_match.group(1)
        if "." not in ticker:
            ticker += ".NS"
        return _get_quote(ticker)

    return _get_indices()


def _get_quote(symbol: str) -> dict:
    try:
        t = yf.Ticker(symbol)
        info = t.fast_info
        current_price = float(info.last_price) if hasattr(info, "last_price") else None
        prev_close = float(info.previous_close) if hasattr(info, "previous_close") else None
        change_pct = round((current_price - prev_close) / prev_close * 100, 2) if current_price and prev_close else None
        return {
            "source": "Yahoo Finance / NSE (live)",
            "symbol": symbol,
            "current_price_inr": current_price,
            "previous_close_inr": prev_close,
            "day_change_pct": change_pct,
            "52w_high": round(float(info.year_high), 2) if hasattr(info, "year_high") else None,
            "52w_low": round(float(info.year_low), 2) if hasattr(info, "year_low") else None,
            "market_cap_cr": round(float(info.market_cap) / 1e7, 0) if hasattr(info, "market_cap") and info.market_cap else None,
            "fetched_at": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


def _get_indices() -> dict:
    results = {}
    for name, symbol in [("Nifty 50", "^NSEI"), ("Sensex", "^BSESN"), ("Nifty Bank", "^NSEBANK")]:
        try:
            t = yf.Ticker(symbol)
            info = t.fast_info
            price = float(info.last_price)
            prev = float(info.previous_close)
            results[name] = {"level": round(price, 2), "change_pct": round((price - prev) / prev * 100, 2)}
        except Exception:
            results[name] = {"error": "unavailable"}
    return {"source": "Yahoo Finance (live)", "indices": results, "fetched_at": datetime.utcnow().isoformat()}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9000)
