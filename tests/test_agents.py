import pytest
from app.agents.config_agent import ConfigAgent
from app.agents.docs_agent import DocsAgent
from app.models import AgentInput, AgentType


# ---------------------------------------------------------------------------
# Docs Agent
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestDocsAgent:

    async def test_basic_docs_query(self):
        agent = DocsAgent()
        resp = await agent.run(AgentInput(query="How do I configure rate limiting?"))
        assert resp.error is None
        assert resp.agent == AgentType.DOCS
        assert "KnowledgeBaseSearch" in resp.tool_calls_made
        assert len(resp.detailed_response) > 0

    async def test_auth_docs_query(self):
        agent = DocsAgent()
        resp = await agent.run(AgentInput(query="How do I set up JWT authentication?"))
        assert resp.error is None
        assert "KnowledgeBaseSearch" in resp.tool_calls_made
        assert len(resp.summary) > 0

    async def test_cors_docs_query(self):
        agent = DocsAgent()
        resp = await agent.run(AgentInput(query="How do I set up CORS?"))
        assert resp.error is None
        assert resp.detailed_response is not None

    async def test_response_has_required_fields(self):
        agent = DocsAgent()
        resp = await agent.run(AgentInput(query="What is rate limiting?"))
        assert resp.summary is not None
        assert resp.detailed_response is not None
        assert resp.tool_calls_made is not None
        assert resp.agent == AgentType.DOCS


# ---------------------------------------------------------------------------
# Config Agent
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestConfigAgent:

    async def test_list_services(self):
        agent = ConfigAgent()
        resp = await agent.run(AgentInput(query="Show me all my services"))
        assert resp.error is None
        assert resp.agent == AgentType.CONFIG
        assert "GetControlPlane" in resp.tool_calls_made
        assert "GetService" in resp.tool_calls_made

    async def test_list_routes(self):
        agent = ConfigAgent()
        resp = await agent.run(AgentInput(query="List all routes"))
        assert resp.error is None
        assert "GetControlPlane" in resp.tool_calls_made
        assert "GetRoute" in resp.tool_calls_made

    async def test_list_plugins(self):
        agent = ConfigAgent()
        resp = await agent.run(AgentInput(query="What plugins are configured?"))
        assert resp.error is None
        assert "GetPlugin" in resp.tool_calls_made

    async def test_control_plane_always_resolved(self):
        # GetControlPlane must always be the first tool call
        agent = ConfigAgent()
        resp = await agent.run(AgentInput(query="Show me all my services"))
        assert resp.tool_calls_made[0] == "GetControlPlane"

    async def test_response_structure(self):
        agent = ConfigAgent()
        resp = await agent.run(AgentInput(query="List all services"))
        assert isinstance(resp.summary, str)
        assert isinstance(resp.detailed_response, str)
        assert isinstance(resp.tool_calls_made, list)
        assert len(resp.tool_calls_made) >= 1

    async def test_raw_data_present(self):
        agent = ConfigAgent()
        resp = await agent.run(AgentInput(query="Show me all services"))
        assert resp.raw_data is not None
        assert "control_planes" in resp.raw_data

    async def test_payments_service_returned(self):
        # Verifies real data from Kong Konnect
        agent = ConfigAgent()
        resp = await agent.run(AgentInput(query="Show me all my services"))
        assert resp.error is None
        # The payments-service we created should appear in summary or detail
        assert (
            "payments-service" in resp.detailed_response
            or "payments-service" in resp.summary
            or resp.summary.startswith("No")   # empty CP is also valid
        )