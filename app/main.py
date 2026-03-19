import json
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from app.agents.config_agent import ConfigAgent
from app.agents.docs_agent import DocsAgent
from app.mcp_client import mcp_client
from app.models import (
    AgentInput,
    AgentType,
    ChatRequest,
    ConversationRole,
    ConversationTurn,
)
from app.router import route
from app.settings import get_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------
_agents = {
    AgentType.CONFIG: ConfigAgent(),
    AgentType.DOCS:   DocsAgent(),
}

# ---------------------------------------------------------------------------
# In-memory session store  { session_id: [ConversationTurn, ...] }
# ---------------------------------------------------------------------------
_sessions: dict[str, list[ConversationTurn]] = {}


# ---------------------------------------------------------------------------
# Lifespan — warm up MCP on startup
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Warming up MCP client...")
    try:
        tools = await mcp_client.list_tools()
        logger.info("MCP ready — %d tools available", len(tools))
    except Exception as exc:
        logger.warning("MCP warm-up failed (will retry on first request): %s", exc)
    yield
    await mcp_client.close()
    logger.info("MCP client closed")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Kong AI Agent Orchestration System",
    description="Multi-agent AI assistant for Kong Gateway infrastructure management",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------
def _sse(event_type: str, **kwargs) -> str:
    """Format a single SSE data line."""
    payload = {"type": event_type, **kwargs}
    return f"data: {json.dumps(payload)}\n\n"


async def _stream_chat(message: str, session_id: str):
    """
    Core streaming generator — yields SSE events in order:
        1. routing  — which agent was chosen and how
        2. chunk    — the detailed response text in one chunk
        3. done     — summary + metadata
       (or error   — on any failure)
    """
    history = _sessions.get(session_id, [])

    try:
        # --- Route ---
        decision = await route(message, history)
        yield _sse(
            "routing",
            agent=decision.agent.value,
            method=decision.method.value,
            confidence=decision.confidence,
            reasoning=decision.reasoning,
        )

        # --- Run agent ---
        agent_input = AgentInput(
            query=message,
            history=history,
            session_id=session_id,
        )
        agent = _agents[decision.agent]
        response = await agent.run(agent_input)

        # --- Stream detailed response as a chunk ---
        yield _sse("chunk", content=response.detailed_response)

        # --- Done event with full metadata ---
        yield _sse(
            "done",
            summary=response.summary,
            agent=response.agent.value,
            routing_method=decision.method.value,
            tool_calls_made=response.tool_calls_made,
            error=response.error,
        )

        # --- Persist turn to session history ---
        if session_id:
            if session_id not in _sessions:
                _sessions[session_id] = []
            _sessions[session_id].append(
                ConversationTurn(role=ConversationRole.USER, content=message)
            )
            _sessions[session_id].append(
                ConversationTurn(
                    role=ConversationRole.ASSISTANT,
                    content=response.summary,
                )
            )
            # Cap history at 20 turns to avoid unbounded memory growth
            _sessions[session_id] = _sessions[session_id][-20:]

    except Exception as exc:
        logger.exception("Unhandled error in stream_chat")
        yield _sse("error", message=str(exc))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.post("/chat")
async def chat(request: ChatRequest):
    """
    Send a message and receive a streaming SSE response.

    If session_id is omitted, a new session is created and returned
    in the first SSE event so the client can reuse it for follow-ups.
    """
    session_id = request.session_id or str(uuid.uuid4())

    return StreamingResponse(
        _stream_chat(request.message, session_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Session-Id": session_id,         # client can read this header
            "Access-Control-Expose-Headers": "X-Session-Id",
        },
    )


@app.get("/health")
async def health():
    """Health check — verifies Ollama and MCP connectivity."""
    ollama_ok = False
    mcp_ok = False

    # Check Ollama
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(base_url=settings.ollama_base_url, api_key="ollama")
        models = await client.models.list()
        ollama_ok = any(settings.ollama_model in m.id for m in models.data)
    except Exception as exc:
        logger.warning("Ollama health check failed: %s", exc)

    # Check MCP (use cached tools if available)
    try:
        tools = await mcp_client.list_tools()
        mcp_ok = len(tools) > 0
    except Exception as exc:
        logger.warning("MCP health check failed: %s", exc)

    return {
        "status": "ok" if ollama_ok and mcp_ok else "degraded",
        "ollama": "connected" if ollama_ok else "unavailable",
        "kong_mcp": "connected" if mcp_ok else "unavailable",
        "tools_cached": len(mcp_client._tools_cache),
        "active_sessions": len(_sessions),
    }


@app.get("/sessions")
async def list_sessions():
    """List all active sessions and their turn counts."""
    return {
        sid: {"turns": len(turns)}
        for sid, turns in _sessions.items()
    }


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """Clear conversation history for a session."""
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    del _sessions[session_id]
    return {"deleted": session_id}


# ---------------------------------------------------------------------------
# Dev entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=True,
    )