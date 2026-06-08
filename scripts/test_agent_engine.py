"""
Quick smoke-test for the deployed FSI-RM Orchestrator on Vertex AI Agent Engine.

Usage:
    python scripts/test_agent_engine.py
    python scripts/test_agent_engine.py --message "Which clients have SIP expiring this month?"
"""

import argparse
import asyncio
import os
import sys

import vertexai
from vertexai import agent_engines

PROJECT = os.getenv("GCP_PROJECT", "<YOUR_GCP_PROJECT>")
LOCATION = os.getenv("GCP_LOCATION", "us-east1")
AGENT_ENGINE_ID = os.getenv("AGENT_ENGINE_ID", "9054979632037625856")


async def run_test(message: str):
    print(f"\n{'='*60}")
    print(f"  Agent Teams for Relationship Managers — Agent Engine Test")
    print(f"{'='*60}")
    print(f"  Project  : {PROJECT}")
    print(f"  Location : {LOCATION}")
    print(f"  Engine ID: {AGENT_ENGINE_ID}")
    print(f"  Query    : {message}")
    print(f"{'='*60}\n")

    vertexai.init(project=PROJECT, location=LOCATION)

    resource_name = (
        f"projects/{PROJECT}/locations/{LOCATION}/reasoningEngines/{AGENT_ENGINE_ID}"
    )
    remote_app = agent_engines.get(resource_name)

    print("Creating session...")
    session = await remote_app.async_create_session(user_id="test-rm-001")
    session_id = session["id"]
    print(f"Session ID: {session_id}\n")

    print("Sending query...\n")
    print("─" * 60)

    full_response = []
    async for event in remote_app.async_stream_query(
        user_id="test-rm-001",
        session_id=session_id,
        message=message,
    ):
        # Events are structured as {content: {parts: [...]}, model_version, ...}
        parts = event.get("content", {}).get("parts", [])
        for part in parts:
            if "text" in part:
                text = part["text"]
                print(text, end="", flush=True)
                full_response.append(text)
            elif "function_call" in part:
                fc = part["function_call"]
                print(f"\n[TOOL CALL] {fc.get('name')}({fc.get('args', {})})", flush=True)
            elif "function_response" in part:
                fr = part["function_response"]
                resp = fr.get("response", {})
                print(f"\n[TOOL RESULT] {fr.get('name')} → {str(resp)[:300]}", flush=True)

    print("\n" + "─" * 60)
    print("\nTest complete.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--message",
        default="What MCP services are connected to this deployment? Use list_mcp_connections.",
        help="Query to send to the orchestrator",
    )
    args = parser.parse_args()
    asyncio.run(run_test(args.message))


if __name__ == "__main__":
    main()
