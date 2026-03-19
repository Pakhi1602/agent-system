import pytest
from app.mcp_client import mcp_client


@pytest.fixture(autouse=True)
async def reset_mcp_client():
    """
    Reset the MCP client before each test so a fresh httpx.AsyncClient
    is created bound to the current test's event loop.
    Prevents 'Event loop is closed' errors across test cases.
    """
    if mcp_client._client and not mcp_client._client.is_closed:
        await mcp_client._client.aclose()
    mcp_client._client = None
    mcp_client._session_id = None
    mcp_client._tools_cache = []
    yield
    if mcp_client._client and not mcp_client._client.is_closed:
        await mcp_client._client.aclose()
    mcp_client._client = None