"""
A2A Client — used by GCP agents to call external AWS Bedrock AgentCore agents.
Implements the A2A Protocol v1.0 JSON-RPC interface with SigV4 authentication.

Usage (from within an ADK agent tool):
    from external_agents.a2a_client import a2a_call

    nav_data = await a2a_call(
        agent_url=os.getenv("AMFI_AGENT_URL"),
        query="Get NAV for HDFC Mid-Cap Opportunities",
    )
"""

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import httpx


def _sigv4_headers(method: str, url: str, body: bytes, region: str = "us-east-1") -> dict:
    """
    Generate AWS SigV4 authorization headers for AgentCore requests.
    Uses boto3/botocore when available; falls back to unsigned requests for local dev.
    """
    try:
        import boto3
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest
        from botocore.credentials import Credentials

        session = boto3.Session()
        creds = session.get_credentials()
        if creds is None:
            return {}
        creds = creds.get_frozen_credentials()

        aws_request = AWSRequest(method=method, url=url, data=body)
        aws_request.headers["Content-Type"] = "application/json"
        SigV4Auth(creds, "bedrock-agentcore", region).add_auth(aws_request)
        return dict(aws_request.headers)
    except ImportError:
        return {"Content-Type": "application/json"}
    except Exception:
        return {"Content-Type": "application/json"}


def _aws_region_from_url(url: str) -> str:
    parsed = urlparse(url)
    parts = parsed.hostname.split(".") if parsed.hostname else []
    for part in parts:
        if part.startswith("us-") or part.startswith("eu-") or part.startswith("ap-"):
            return part
    return os.environ.get("AWS_DEFAULT_REGION", "us-east-1")


async def a2a_call(
    agent_url: str,
    query: str,
    timeout: int = 30,
    session_id: Optional[str] = None,
) -> dict:
    """
    Send a message to an A2A-compatible AgentCore agent and return the result.
    Uses SigV4 authentication and the A2A v1.0 message/send JSON-RPC method.
    """
    if not agent_url:
        return {"error": "Agent URL not configured", "query": query}

    request_id = str(uuid.uuid4())
    # AgentCore requires session ID ≥ 33 chars
    sid = session_id or str(uuid.uuid4())

    payload = {
        "jsonrpc": "2.0",
        "method": "message/send",
        "id": request_id,
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": query}],
            },
        },
    }

    body = json.dumps(payload).encode("utf-8")
    region = _aws_region_from_url(agent_url)
    headers = _sigv4_headers("POST", agent_url, body, region)
    headers["X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"] = sid

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(agent_url, content=body, headers=headers)
            resp.raise_for_status()
            body_resp = resp.json()

            artifacts = body_resp.get("result", {}).get("artifacts", [])
            if artifacts:
                parts = artifacts[0].get("parts", [])
                if parts:
                    text = parts[0].get("text", "")
                    try:
                        return json.loads(text)
                    except Exception:
                        return {"raw": text}

            return body_resp.get("result", {})

    except httpx.TimeoutException:
        return {"error": f"Agent at {agent_url} timed out after {timeout}s", "query": query}
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}", "agent_url": agent_url}
    except Exception as e:
        return {"error": str(e), "agent_url": agent_url, "query": query}


async def get_agent_card(agent_url: str) -> dict:
    """Fetch the A2A Agent Card from /.well-known/agent-card.json."""
    base_url = agent_url.split("/invocations")[0]
    card_url = f"{base_url.rstrip('/')}/.well-known/agent-card.json"
    region = _aws_region_from_url(agent_url)
    headers = _sigv4_headers("GET", card_url, b"", region)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(card_url, headers=headers)
            return resp.json()
    except Exception as e:
        return {"error": str(e)}
