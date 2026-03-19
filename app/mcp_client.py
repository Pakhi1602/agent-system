import httpx
import logging
import time
from typing import Any
from app.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class MCPError(Exception):
    """Raised when an MCP tool call fails."""
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


class MCPClient:
    """
    Manages a single MCP session with the Konnect MCP server.

    Lifecycle:
        1. POST /  with method=initialize  → captures mcp-session-id header
        2. All subsequent tool calls pass that header
        3. Session is refreshed when TTL expires or on auth error

    The httpx.AsyncClient is created lazily on first use so it always
    binds to the currently running event loop. This is required for
    pytest-asyncio which creates a new loop per test.
    """

    def __init__(self):
        self._session_id: str | None = None
        self._session_created_at: float = 0.0
        self._tools_cache: list[dict[str, Any]] = []
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Client management (lazy init)
    # ------------------------------------------------------------------

    def _get_client(self) -> httpx.AsyncClient:
        """Return existing client or create one bound to current event loop."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=settings.mcp_tool_timeout)
            # New client = new event loop context, reset session state
            self._session_id = None
            self._tools_cache = []
        return self._client

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    async def _initialize_session(self) -> None:
        """Perform MCP handshake and store the session ID."""
        logger.info("Initialising MCP session with %s", settings.kong_mcp_url)
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "kong-agent-system", "version": "1.0.0"},
            },
        }
        client = self._get_client()
        response = await client.post(
            settings.kong_mcp_url,
            json=payload,
            headers=self._base_headers(),
        )
        response.raise_for_status()

        session_id = response.headers.get("mcp-session-id")
        if not session_id:
            raise MCPError("No mcp-session-id returned during initialize")

        self._session_id = session_id
        self._session_created_at = time.monotonic()
        self._tools_cache = []
        logger.info("MCP session established: %s", session_id)

    async def _ensure_session(self) -> None:
        """Create or refresh the session if missing or expired."""
        age = time.monotonic() - self._session_created_at
        if self._session_id is None or age > settings.mcp_session_ttl:
            await self._initialize_session()

    def _base_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {settings.kong_pat}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

    def _session_headers(self) -> dict[str, str]:
        headers = self._base_headers()
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        return headers

    # ------------------------------------------------------------------
    # Tool discovery
    # ------------------------------------------------------------------

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return cached tool definitions, fetching once per session."""
        await self._ensure_session()

        if self._tools_cache:
            return self._tools_cache

        logger.info("Fetching tool list from MCP server")
        payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        client = self._get_client()
        response = await client.post(
            settings.kong_mcp_url,
            json=payload,
            headers=self._session_headers(),
        )
        response.raise_for_status()

        data = response.json()
        tools = data.get("result", {}).get("tools", [])
        self._tools_cache = tools
        logger.info("Cached %d MCP tools", len(tools))
        return tools

    def tool_names(self) -> list[str]:
        """Synchronous helper — returns names from cache (call list_tools first)."""
        return [t["name"] for t in self._tools_cache]

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    async def call_tool(
        self,
        tool_name: str,
        params: dict[str, Any],
        *,
        retry_on_auth_error: bool = True,
    ) -> dict[str, Any]:
        """
        Execute a single MCP tool and return its result dict.

        Automatically re-initialises the session on 401/403 and retries once.
        """
        await self._ensure_session()
        logger.info("Calling MCP tool: %s  params=%s", tool_name, params)

        payload = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": params},
        }

        client = self._get_client()
        try:
            response = await client.post(
                settings.kong_mcp_url,
                json=payload,
                headers=self._session_headers(),
            )
        except httpx.TimeoutException:
            raise MCPError(
                f"Tool {tool_name!r} timed out after {settings.mcp_tool_timeout}s"
            )
        except httpx.RequestError as exc:
            raise MCPError(f"Network error calling {tool_name!r}: {exc}")

        # Re-init and retry once on auth failure
        if response.status_code in (401, 403) and retry_on_auth_error:
            logger.warning(
                "Auth error (%s) — re-initialising session and retrying",
                response.status_code,
            )
            self._session_id = None
            return await self.call_tool(tool_name, params, retry_on_auth_error=False)

        if response.status_code == 404:
            raise MCPError(
                f"Tool {tool_name!r} not found on MCP server", code=404
            )

        if not response.is_success:
            raise MCPError(
                f"MCP server returned HTTP {response.status_code} "
                f"for tool {tool_name!r}: {response.text[:200]}",
                code=response.status_code,
            )

        data = response.json()

        # JSON-RPC level error
        if "error" in data:
            err = data["error"]
            raise MCPError(
                f"Tool {tool_name!r} returned JSON-RPC error "
                f"{err.get('code')}: {err.get('message')}",
                code=err.get("code"),
            )

        result = data.get("result", {})
        logger.info("Tool %s succeeded", tool_name)
        return result

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


# ---------------------------------------------------------------------------
# Module-level singleton — imported by agents and router
# ---------------------------------------------------------------------------
mcp_client = MCPClient()