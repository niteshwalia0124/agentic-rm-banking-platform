"""
Deploy the FSI-RM orchestrator to Vertex AI Agent Engine (Agent Runtime).

Replaces adk.yaml with a programmatic deploy that enables:
  - Agent Identity  (principalSet:// identity, not a service account)
  - Agent Gateway   (REQUEST_AUTHZ via IAP + CONTENT_AUTHZ via Model Armor)
  - PSC network     (Private Service Connect for internal MCP routing)
  - OTel telemetry  (via InstrumentedAdkApp)

Adapted from:
  GoogleCloudPlatform/cloud-networking-solutions/demos/agent-gateway/src/mortgage-agent/deploy_agent.py

Usage:
  # First deploy (creates a new Agent Engine resource)
  python agents/orchestrator/deploy_agent.py \
    --project=<YOUR_GCP_PROJECT> \
    --location=asia-south1 \
    --agent-gateway=projects/<YOUR_GCP_PROJECT>/locations/asia-south1/agentGateways/fsi-rm-gateway \
    --network-attachment=projects/<YOUR_GCP_PROJECT>/regions/asia-south1/networkAttachments/fsi-rm-psc \
    --invoker-sa=agent-mcp-invoker@<YOUR_GCP_PROJECT>.iam.gserviceaccount.com \
    --enable-agent-identity

  # Update an existing deployment
  python agents/orchestrator/deploy_agent.py ... --update --agent-id=<AGENT_ENGINE_ID>

  # Deploy without Agent Gateway (local testing against Agent Engine)
  python agents/orchestrator/deploy_agent.py --project=<YOUR_GCP_PROJECT> --location=asia-south1
"""

import argparse
import copy
import os
import sys

import vertexai
from vertexai import agent_engines
from vertexai.preview.reasoning_engines import AdkApp

# Import the built agent — discovery runs at import time.
# On Agent Engine the agent is unpickled via _PickleSafeOrchestrator.__reduce__
# which re-runs _build_orchestrator() with fresh Agent Registry discovery.
from agents.orchestrator.agent import root_agent


def _parse_args():
    p = argparse.ArgumentParser(description="Deploy FSI-RM orchestrator to Agent Engine")
    p.add_argument("--project", default=os.environ.get("GCP_PROJECT", ""))
    p.add_argument("--location", default=os.environ.get("GCP_LOCATION", "asia-south1"))
    p.add_argument("--agent-gateway", default="", help="Full resource name of the Agent Gateway")
    p.add_argument("--network-attachment", default="", help="PSC network attachment resource name")
    p.add_argument("--dns-domain", default="", help="Private DNS domain for MCP services (optional)")
    p.add_argument("--invoker-sa", default="", help="SA email the agent impersonates for MCP Cloud Run calls")
    p.add_argument("--enable-agent-identity", action="store_true", help="Enable Agent Identity on the deployment")
    p.add_argument("--update", action="store_true", help="Update existing deployment instead of creating")
    p.add_argument("--agent-id", default="", help="Agent Engine resource ID (required with --update)")
    p.add_argument("--model", default=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"))
    p.add_argument("--staging-bucket", default="", help="GCS bucket for deploy staging (defaults to {project}-agent-engine-staging)")
    return p.parse_args()


class AgentEngineApp(AdkApp):
    """
    AdkApp subclass that wires up OTel tracing and Cloud Logging at container
    startup. The set_up() lifecycle hook runs inside the deployed Agent Engine
    container — that's where the TracerProvider must be created and
    enable_tracing flipped on, so OTel gen_ai.* spans flow to Cloud Trace and
    populate the Agent Engine → Traces tab.
    """

    APP_NAME = "fsi_rm_orchestrator"

    def set_up(self) -> None:
        # Gemini 3.5 Flash and Gemini 3.1 Flash Live are only served from the
        # global endpoint. Agent Engine auto-injects GOOGLE_CLOUD_LOCATION with
        # the deployment region (us-east1) and rejects it in deploy-time env
        # config as "reserved" — so we override in-process at startup, before
        # ADK constructs its genai client on first model call.
        os.environ["GOOGLE_CLOUD_LOCATION"] = "global"

        # Hack to avoid name validation error
        engine_id = os.environ.pop("GOOGLE_CLOUD_AGENT_ENGINE_ID", None)
        self._tmpl_attrs["app_name"] = self.APP_NAME

        super().set_up()

        if engine_id:
            os.environ["GOOGLE_CLOUD_AGENT_ENGINE_ID"] = engine_id
            self._rewire_for_memory_bank(engine_id)

        try:
            from google.cloud import logging as google_cloud_logging
            logging_client = google_cloud_logging.Client()
            self.logger = logging_client.logger(__name__)
        except Exception:
            pass

        self.enable_tracing = True

    def _rewire_for_memory_bank(self, engine_id: str) -> None:
        """Re-create Runner with VertexAiSessionService + VertexAiMemoryBankService.

        super().set_up() built it with in-memory services because we hid the
        engine ID from it. Now wire in the real Vertex services and rebuild.
        """
        from google.adk.runners import Runner
        from google.adk.memory.vertex_ai_memory_bank_service import (
            VertexAiMemoryBankService,
        )
        from google.adk.sessions.vertex_ai_session_service import (
            VertexAiSessionService,
        )

        project = self._tmpl_attrs.get("project")
        location = self._tmpl_attrs.get("location")

        self._tmpl_attrs["session_service"] = VertexAiSessionService(
            project=project, location=location, agent_engine_id=engine_id,
        )
        self._tmpl_attrs["memory_service"] = VertexAiMemoryBankService(
            project=project, location=location, agent_engine_id=engine_id,
        )
        self._tmpl_attrs["runner"] = Runner(
            agent=self._tmpl_attrs.get("agent"),
            plugins=self._tmpl_attrs.get("plugins"),
            session_service=self._tmpl_attrs["session_service"],
            artifact_service=self._tmpl_attrs.get("artifact_service"),
            memory_service=self._tmpl_attrs["memory_service"],
            app_name=self.APP_NAME,
        )

    def clone(self) -> "AgentEngineApp":
        t = self._tmpl_attrs
        return self.__class__(
            agent=copy.deepcopy(t["agent"]),
            enable_tracing=bool(t.get("enable_tracing", False)),
            session_service_builder=t.get("session_service_builder"),
            artifact_service_builder=t.get("artifact_service_builder"),
            env_vars=t.get("env_vars"),
        )


def main():
    args = _parse_args()

    if not args.project:
        print("ERROR: --project is required (or set GCP_PROJECT)", file=sys.stderr)
        sys.exit(1)

    staging_bucket = args.staging_bucket or f"gs://{args.project}-agent-engine-staging"
    vertexai.init(project=args.project, location=args.location, staging_bucket=staging_bucket)

    # ── Runtime environment variables ──────────────────────────────────────────
    # These land in the Agent Engine container at runtime. The agent reads them
    # to discover MCP servers from Agent Registry and authenticate via impersonation.
    # NOTE: GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_LOCATION are reserved by
    # Agent Engine — it auto-injects them. We use MCP_REGISTRY_* instead.
    env_vars = {
        "MCP_REGISTRY_PROJECT": args.project,
        "MCP_REGISTRY_LOCATION": args.location,
        "GEMINI_MODEL": args.model,
        # Agent Engine telemetry
        "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "true",
        # AWS credentials for cross-cloud A2A calls to Bedrock AgentCore
        "AWS_ACCESS_KEY_ID": os.environ.get("AWS_ACCESS_KEY_ID", ""),
        "AWS_SECRET_ACCESS_KEY": os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
        "AWS_DEFAULT_REGION": os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        # External A2A agent URLs (Bedrock AgentCore, us-east-1)
        "AMFI_AGENT_URL": os.environ.get("AMFI_AGENT_URL", ""),
        "MARKET_DATA_AGENT_URL": os.environ.get("MARKET_DATA_AGENT_URL", ""),
        "CREDIT_BUREAU_AGENT_URL": os.environ.get("CREDIT_BUREAU_AGENT_URL", ""),
        "ACCOUNT_AGGREGATOR_AGENT_URL": os.environ.get("ACCOUNT_AGGREGATOR_AGENT_URL", ""),
    }
    if args.invoker_sa:
        env_vars["MCP_INVOKER_SA_EMAIL"] = args.invoker_sa

    # Drop env vars with empty values. The Agent Engine update API rejects
    # them with "Required field is not set" (create is more lenient). An
    # empty env var is functionally equivalent to it being absent anyway.
    env_vars = {k: v for k, v in env_vars.items() if v}

    # Build the AdkApp subclass that wires OTel tracing in set_up() at container startup
    app = AgentEngineApp(agent=root_agent, enable_tracing=True, env_vars=env_vars)

    # ── Deployment config ──────────────────────────────────────────────────────
    config = {
        "agent_engine": app,
        "display_name": "FSI-RM Orchestrator",
        "description": (
            "Root agent for the Indian bank Relationship Manager AI system. "
            "Routes RM queries to 5 specialist sub-agents: client intel, portfolio, "
            "comms, compliance, and voice (11 Indian languages via Gemini Live). "
            "Persists cross-session context via Vertex AI Memory Bank."
        ),
        "env_vars": env_vars,
        # Package our source tree into the Agent Engine container
        "extra_packages": ["agents", "external_agents", "mcp_servers"],
        # Pin ADK version with required extras for Agent Registry and Agent Identity
        "requirements": [
            "google-adk[a2a,agent-identity]>=2.0.0",
            "mcp>=1.0.0",
            "google-cloud-aiplatform[agent_engines]>=1.70.0",
            "google-cloud-bigquery>=3.25.0",
            "google-cloud-logging>=3.10.0",
            "opentelemetry-api>=1.27.0",
            "opentelemetry-sdk>=1.27.0",
            "opentelemetry-exporter-gcp-trace>=1.7.0",
            # google-genai instrumentation is the one that emits the
            # gen_ai.* spans that populate the Agent Engine Traces tab.
            # We deliberately DO NOT install opentelemetry-instrumentation-httpx
            # or -grpc: httpx instrumentation wraps the async client used by
            # MCPToolset(StreamableHTTPConnectionParams) and breaks streaming
            # MCP sessions with "unhandled errors in a TaskGroup". Losing
            # httpx/grpc spans is a small cost for working MCP transport.
            "opentelemetry-instrumentation-google-genai>=0.2b0",
            "httpx>=0.27.0",
            "websockets>=12.0",
            "fastapi>=0.115.0",
            "uvicorn[standard]>=0.30.0",
            "boto3>=1.34.0",
            "botocore>=1.34.0",
        ],
        "resource_limits": {"cpu": "4", "memory": "8Gi"},
        "min_instances": 2,
    }

    # Agent Identity — gives the deployment a principalSet:// identity for IAP
    if args.enable_agent_identity:
        config["identity_type"] = "AGENT_IDENTITY"
        print("Agent Identity: ENABLED")

    # Agent Gateway — routes all MCP traffic through IAP + Model Armor
    if args.agent_gateway:
        config["agent_gateway_config"] = {
            "agent_to_anywhere_config": {"agent_gateway": args.agent_gateway}
        }
        print(f"Agent Gateway: {args.agent_gateway}")

    # PSC network attachment — keeps MCP traffic on private VPC
    if args.network_attachment:
        psc_config: dict = {"network_attachment": args.network_attachment}
        if args.dns_domain:
            psc_config["dns_peering_configs"] = [{"domain": args.dns_domain}]
        config["psc_interface_config"] = psc_config
        print(f"PSC network: {args.network_attachment}")

    # ── Create or update ───────────────────────────────────────────────────────
    if args.update:
        if not args.agent_id:
            print("ERROR: --agent-id is required with --update", file=sys.stderr)
            sys.exit(1)

        existing = agent_engines.get(args.agent_id)
        existing.update(**config)
        print(f"\nUpdated Agent Engine: {args.agent_id}")
        agent_id = args.agent_id
    else:
        engine = agent_engines.create(**config)
        agent_id = engine.resource_name
        print(f"\nCreated Agent Engine: {agent_id}")

    # ── Post-deploy instructions ───────────────────────────────────────────────
    print("\n── Next steps ──────────────────────────────────────────────────────")
    print(f"1. Set AGENT_ENGINE_ID={agent_id}")
    print("2. Run Terraform to grant Agent Identity IAM bindings:")
    print("     terraform apply -var agent_engine_id=<id>")
    print("3. Grant per-MCP egress IAM (if using Agent Gateway):")
    print("     bash scripts/grant_agent_mcp_egress.sh")
    print("4. Register in Gemini Enterprise Agentspace:")
    print("     See gateway/register_in_agentspace.md")
    print("5. Test via Agent Platform console playground")
    print("────────────────────────────────────────────────────────────────────")


if __name__ == "__main__":
    main()
