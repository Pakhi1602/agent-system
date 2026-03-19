import pytest
import uuid
from httpx import AsyncClient, ASGITransport
from app.main import app, _sessions
from app.models import ConversationRole, ConversationTurn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_sse(raw: str) -> list[dict]:
    """Parse raw SSE text into a list of event dicts."""
    import json
    events = []
    for line in raw.strip().splitlines():
        line = line.strip()
        if line.startswith("data:"):
            try:
                events.append(json.loads(line[len("data:"):].strip()))
            except Exception:
                pass
    return events


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestSessionManagement:

    async def test_new_session_created_when_omitted(self):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/chat", json={"message": "Show me all my services"}
            )
            assert resp.status_code == 200
            # Server returns session ID in header
            assert "x-session-id" in resp.headers

    async def test_provided_session_id_is_used(self):
        session_id = f"test-{uuid.uuid4()}"
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/chat",
                json={"message": "Show me all my services", "session_id": session_id},
            )
            assert resp.status_code == 200
            assert resp.headers.get("x-session-id") == session_id

    async def test_session_stored_after_turn(self):
        session_id = f"test-{uuid.uuid4()}"
        _sessions.pop(session_id, None)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.post(
                "/chat",
                json={"message": "Show me all my services", "session_id": session_id},
            )

        assert session_id in _sessions
        assert len(_sessions[session_id]) == 2   # user + assistant turns

    async def test_delete_session(self):
        session_id = f"test-{uuid.uuid4()}"
        _sessions[session_id] = [
            ConversationTurn(role=ConversationRole.USER, content="hello")
        ]
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete(f"/sessions/{session_id}")
            assert resp.status_code == 200
            assert resp.json()["deleted"] == session_id
        assert session_id not in _sessions

    async def test_delete_nonexistent_session_returns_404(self):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete("/sessions/does-not-exist")
            assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Multi-turn context
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestMultiTurnConversation:

    async def test_history_grows_across_turns(self):
        session_id = f"test-{uuid.uuid4()}"
        _sessions.pop(session_id, None)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Turn 1
            await client.post(
                "/chat",
                json={"message": "Show me all my services", "session_id": session_id},
            )
            assert len(_sessions.get(session_id, [])) == 2

            # Turn 2
            await client.post(
                "/chat",
                json={"message": "What routes does it have?", "session_id": session_id},
            )
            assert len(_sessions.get(session_id, [])) == 4

    async def test_sse_events_sequence(self):
        """Every response must emit routing → chunk → done in that order."""
        session_id = f"test-{uuid.uuid4()}"
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/chat",
                json={"message": "Show me all my services", "session_id": session_id},
            )
        events = _parse_sse(resp.text)
        types = [e["type"] for e in events]
        assert types[0] == "routing"
        assert "chunk" in types
        assert types[-1] == "done"

    async def test_done_event_has_metadata(self):
        session_id = f"test-{uuid.uuid4()}"
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/chat",
                json={"message": "Show me all my services", "session_id": session_id},
            )
        events = _parse_sse(resp.text)
        done = next(e for e in events if e["type"] == "done")
        assert "summary" in done
        assert "agent" in done
        assert "routing_method" in done
        assert "tool_calls_made" in done

    async def test_routing_event_has_agent_and_method(self):
        session_id = f"test-{uuid.uuid4()}"
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/chat",
                json={"message": "How do I configure rate limiting?", "session_id": session_id},
            )
        events = _parse_sse(resp.text)
        routing = next(e for e in events if e["type"] == "routing")
        assert routing["agent"] in ("config-agent", "docs-agent")
        assert routing["method"] in ("rule_based", "llm_fallback")

    async def test_session_history_passed_to_followup(self):
        """Follow-up query should carry history from first turn."""
        session_id = f"test-{uuid.uuid4()}"
        _sessions.pop(session_id, None)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # First turn
            await client.post(
                "/chat",
                json={"message": "Show me all my services", "session_id": session_id},
            )
            # Second turn — follow-up
            resp = await client.post(
                "/chat",
                json={"message": "What routes does it have?", "session_id": session_id},
            )

        events = _parse_sse(resp.text)
        done = next(e for e in events if e["type"] == "done")
        # Follow-up should resolve to config agent (routes query)
        assert done["agent"] == "config-agent"
        assert done["error"] is None


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestHealthEndpoint:

    async def test_health_returns_ok(self):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "ollama" in data
        assert "kong_mcp" in data