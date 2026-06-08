import sys
import os

# Agent Engine stages code in a subdirectory — add it to sys.path so that
# 'agents' and 'external_agents' packages are importable.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# gemini-3.5-flash and gemini-3.1-flash-live-preview are only available at
# the global Vertex AI endpoint. The ADK CLI overrides GOOGLE_CLOUD_LOCATION
# with --region (us-east1) in the container, so we force it here before the
# Vertex AI SDK initializes.
os.environ["GOOGLE_CLOUD_LOCATION"] = "global"

from agents.orchestrator.agent import root_agent  # noqa: E402

__all__ = ["root_agent"]
