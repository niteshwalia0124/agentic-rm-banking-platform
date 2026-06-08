"""
Shared Agent Registry utilities for all FSI-RM sub-agents.

Two operating modes:
  Production  — MCP_REGISTRY_PROJECT is set → discovers MCP servers from
                Agent Registry at startup; uses Agent Identity impersonation
                chain to authenticate against IAP-protected Cloud Run services.
  Local dev   — MCP_REGISTRY_PROJECT is unset → falls back to *_MCP_URL env
                vars so scripts/start_local.sh continues to work unchanged.

Key patterns adapted from:
  GoogleCloudPlatform/cloud-networking-solutions/demos/agent-gateway
"""

import logging
import os
from typing import Any
from urllib.parse import urlparse

import httpx

log = logging.getLogger("fsi_rm.registry")

# ── Environment knobs ─────────────────────────────────────────────────────────

MCP_REGISTRY_PROJECT = os.environ.get("MCP_REGISTRY_PROJECT") or os.environ.get(
    "GOOGLE_CLOUD_PROJECT"
)
MCP_REGISTRY_LOCATION = os.environ.get("MCP_REGISTRY_LOCATION") or os.environ.get(
    "GOOGLE_CLOUD_LOCATION", "global"
)
# SA the agent impersonates to mint Cloud Run OIDC tokens.
# Agent Identity (principalSet://...) cannot directly invoke Cloud Run — the
# impersonation hop lets us use a standard IAM SA as the Cloud Run caller.
MCP_INVOKER_SA_EMAIL = os.environ.get("MCP_INVOKER_SA_EMAIL", "")

# Track every discovered server so list_mcp_connections() can expose them.
DISCOVERED_MCP_SERVERS: list[dict[str, Any]] = []

# ── Impersonation factory ─────────────────────────────────────────────────────

def build_impersonation_factory(target_url: str, target_sa_email: str):
    """
    Return an httpx_client_factory that signs requests as `target_sa_email`.

    Cloud Run rejects the agent's principalSet identity directly; instead the
    agent impersonates a regular IAM SA and mints an OIDC ID token for it.
    The agent identity must hold roles/iam.serviceAccountTokenCreator on
    `target_sa_email`, and that SA must hold roles/run.invoker on the service.
    """
    import google.auth
    import google.auth.transport.requests as gar
    from google.auth import impersonated_credentials

    parsed = urlparse(target_url)
    audience = f"{parsed.scheme}://{parsed.netloc}"

    try:
        source_creds, source_project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        impersonated = impersonated_credentials.Credentials(
            source_credentials=source_creds,
            target_principal=target_sa_email,
            target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        id_token_creds = impersonated_credentials.IDTokenCredentials(
            target_credentials=impersonated,
            target_audience=audience,
            include_email=True,
        )
        log.info(
            "Impersonation factory ready: target_sa=%s audience=%s source=%s project=%s",
            target_sa_email, audience, type(source_creds).__name__, source_project,
        )
    except Exception:
        log.exception(
            "Failed to build impersonated credentials for %s → %s. "
            "Check: agent identity has roles/iam.serviceAccountTokenCreator, "
            "iamcredentials.googleapis.com is enabled.",
            target_sa_email, audience,
        )
        raise

    class _OIDCAuth(httpx.Auth):
        requires_request_body = False

        def __init__(self, creds):
            self._creds = creds
            self._req = gar.Request()

        def auth_flow(self, request):
            try:
                if not self._creds.valid:
                    self._creds.refresh(self._req)
            except Exception:
                log.exception("OIDC token refresh failed for audience=%s", audience)
                raise
            request.headers["Authorization"] = f"Bearer {self._creds.token}"
            yield request

    auth_handler = _OIDCAuth(id_token_creds)

    def factory(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            follow_redirects=True,
            headers=headers,
            timeout=timeout if timeout is not None else httpx.Timeout(10.0),
            auth=auth if auth is not None else auth_handler,
        )

    return factory


# ── Agent Registry discovery ──────────────────────────────────────────────────

def discover_mcp_toolset(display_name: str, fallback_env_var: str, fallback_default: str):
    """
    Discover a single named MCP server from Agent Registry and return its toolset.

    Falls back to a hardcoded URL (for local dev) when MCP_REGISTRY_PROJECT
    is not set or when the registry returns nothing for this display_name.

    display_name: matches Agent Registry `displayName`, e.g. "FSI-RM Core Banking MCP"
    fallback_env_var: env var name for local URL, e.g. "CORE_BANKING_MCP_URL"
    fallback_default: default localhost URL, e.g. "http://localhost:8001"
    """
    if MCP_REGISTRY_PROJECT and MCP_REGISTRY_LOCATION not in ("", "global"):
        toolset = _try_registry(display_name)
        if toolset is not None:
            return toolset
        log.warning(
            "Registry discovery found nothing for '%s' — falling back to env var %s",
            display_name, fallback_env_var,
        )

    return _fallback_toolset(fallback_env_var, fallback_default, display_name)


def _try_registry(display_name: str):
    """Attempt discovery from Agent Registry. Returns toolset or None."""
    try:
        from google.adk.integrations.agent_registry import AgentRegistry
    except ImportError as e:
        log.warning("AgentRegistry import failed (%s); using fallback URL", e)
        return None

    import time
    max_attempts = 5
    delay = 2

    for attempt in range(max_attempts):
        try:
            registry = AgentRegistry(
                project_id=MCP_REGISTRY_PROJECT,
                location=MCP_REGISTRY_LOCATION,
            )
            response = registry.list_mcp_servers(filter_str=f'displayName="{display_name}"')
            
            servers = response.get("mcpServers", [])
            if not servers:
                return None

            server = servers[0]
            name = server.get("name")
            toolset = registry.get_mcp_toolset(mcp_server_name=name)

            # Inject impersonation auth if invoker SA is configured
            conn_params = getattr(toolset, "_connection_params", None)
            resolved_url = getattr(conn_params, "url", None)
            if MCP_INVOKER_SA_EMAIL and conn_params is not None and resolved_url:
                try:
                    conn_params.httpx_client_factory = build_impersonation_factory(
                        target_url=resolved_url,
                        target_sa_email=MCP_INVOKER_SA_EMAIL,
                    )
                except Exception:
                    log.warning(
                        "Impersonation setup failed for %s — proceeding with default credentials",
                        display_name,
                    )

            DISCOVERED_MCP_SERVERS.append({
                "name": server.get("displayName") or name,
                "resource_name": name,
                "tool_name_prefix": getattr(toolset, "tool_name_prefix", None),
                "resolved_url": resolved_url,
            })

            log.info("Registry: discovered '%s' prefix=%s url=%s",
                     display_name, getattr(toolset, "tool_name_prefix", None), resolved_url)
            return toolset

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                if attempt < max_attempts - 1:
                    log.warning(
                        "Rate limited by Agent Registry for '%s', retrying in %d seconds... (Attempt %d/%d)",
                        display_name, delay, attempt + 1, max_attempts
                    )
                    time.sleep(delay)
                    delay *= 2
                    continue
                else:
                    log.error("Max retry attempts reached for Agent Registry discovery of '%s'", display_name)
                    raise
            else:
                raise
        except Exception:
            log.exception(
                "Agent Registry list failed for '%s' (%s/%s)",
                display_name, MCP_REGISTRY_PROJECT, MCP_REGISTRY_LOCATION,
            )
            return None


def _fallback_toolset(env_var: str, default: str, display_name: str):
    """Build an MCPToolset from env var URL.

    On Cloud Run (K_SERVICE is set) the MCP servers are IAM-protected, so we
    inject an OIDC ID token via the GCE metadata server.  In local dev (no
    K_SERVICE) no auth is added, matching the plain-HTTP localhost servers.
    """
    from google.adk.tools.mcp_tool import MCPToolset, StreamableHTTPConnectionParams

    url = os.getenv(env_var, default)
    mcp_url = f"{url}/mcp"
    log.info("Fallback toolset: '%s' → %s", display_name, mcp_url)

    DISCOVERED_MCP_SERVERS.append({
        "name": display_name,
        "resource_name": None,
        "tool_name_prefix": None,
        "resolved_url": mcp_url,
    })

    conn = StreamableHTTPConnectionParams(url=mcp_url)

    # On Cloud Run inject OIDC auth — metadata server is always available.
    if os.getenv("K_SERVICE"):
        parsed = urlparse(url)
        audience = f"{parsed.scheme}://{parsed.netloc}"
        if MCP_INVOKER_SA_EMAIL:
            try:
                conn.httpx_client_factory = build_impersonation_factory(
                    target_url=url, target_sa_email=MCP_INVOKER_SA_EMAIL
                )
                log.info("Fallback '%s': using impersonation auth (SA=%s)", display_name, MCP_INVOKER_SA_EMAIL)
            except Exception:
                log.warning("Impersonation failed for '%s', falling back to metadata OIDC", display_name)
                conn.httpx_client_factory = _build_metadata_oidc_factory(audience)
        else:
            conn.httpx_client_factory = _build_metadata_oidc_factory(audience)
            log.info("Fallback '%s': using metadata server OIDC (audience=%s)", display_name, audience)

    return MCPToolset(connection_params=conn)


def _build_metadata_oidc_factory(audience: str):
    """httpx_client_factory that fetches OIDC tokens from the GCE metadata server."""
    import urllib.request as _urllib

    class _MetadataAuth(httpx.Auth):
        _token: str = ""

        def auth_flow(self, request):
            if not self._token:
                meta_url = (
                    "http://metadata.google.internal/computeMetadata/v1/"
                    f"instance/service-accounts/default/identity?audience={audience}"
                )
                req = _urllib.Request(meta_url, headers={"Metadata-Flavor": "Google"})
                with _urllib.urlopen(req, timeout=5) as resp:
                    self.__class__._token = resp.read().decode()
            request.headers["Authorization"] = f"Bearer {self._token}"
            yield request

    auth = _MetadataAuth()

    def factory(headers=None, timeout=None, auth_override=None):
        return httpx.AsyncClient(
            follow_redirects=True,
            headers=headers,
            timeout=timeout or httpx.Timeout(30.0),
            auth=auth_override or auth,
        )

    return factory


# ── 403 error handler (shared callback for all agents) ────────────────────────

_DENIED_TOOLS_KEY = "_denied_mcp_tools"
_MAX_403_ATTEMPTS = 2


def handle_tool_error(tool, args: dict, tool_context, error: Exception) -> dict | None:
    """
    Callback for on_tool_error_callback.

    Returns a user-facing error dict for 403 (Agent Gateway policy denial);
    returns None for all other errors so ADK uses its default handling.
    After _MAX_403_ATTEMPTS denials for the same tool, instructs the LLM
    not to call that tool again in this session.
    """
    if not _find_http_403(error):
        return None

    denied: dict = dict(tool_context.state.get(_DENIED_TOOLS_KEY, {}))
    attempts = denied.get(tool.name, 0) + 1
    denied[tool.name] = attempts
    tool_context.state[_DENIED_TOOLS_KEY] = denied

    log.warning(
        "Tool '%s' denied by Agent Gateway (403) — attempt %d/%d",
        tool.name, attempts, _MAX_403_ATTEMPTS,
    )

    if attempts >= _MAX_403_ATTEMPTS:
        return {
            "error": (
                f"Tool '{tool.name}' was denied by the authorization gateway "
                f"{attempts} times. Do not call this tool again — report the "
                "denial to the RM and proceed with available tools."
            )
        }
    return {
        "error": (
            f"Tool '{tool.name}' was denied by the Agent Gateway. "
            "This operation is not permitted by the current IAP policy."
        )
    }


def _find_http_403(exc: BaseException) -> bool:
    """Traverse exception chain and ExceptionGroups looking for an HTTP 403."""
    seen: set[int] = set()
    queue: list[BaseException] = [exc]
    while queue:
        cur = queue.pop()
        if id(cur) in seen:
            continue
        seen.add(id(cur))
        if isinstance(cur, httpx.HTTPStatusError) and cur.response.status_code == 403:
            return True
        if cur.__cause__:
            queue.append(cur.__cause__)
        if cur.__context__:
            queue.append(cur.__context__)
        if isinstance(cur, BaseExceptionGroup):
            queue.extend(cur.exceptions)
    return False


# ── Utility tool ──────────────────────────────────────────────────────────────

def list_mcp_connections() -> dict:
    """Show which MCP servers were discovered (from Agent Registry or local dev)."""
    # Lazy import avoids circular dependency if called from agent code at import time
    return {
        "connections": DISCOVERED_MCP_SERVERS,
        "count": len(DISCOVERED_MCP_SERVERS),
        "registry_project": MCP_REGISTRY_PROJECT or "local-dev",
        "registry_location": MCP_REGISTRY_LOCATION,
    }
