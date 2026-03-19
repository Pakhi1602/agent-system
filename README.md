# kong-agent-system
Read through and understand the exercise to be performed.
agent-system/documents/Take-Home Exercise_ AI Agent Orchestration System (1) (2).docx


now based on the above exercise, claude has generated below architecture diagram and setup.

# Kong AI Agent Orchestration System

An AI-powered assistant that helps users manage Kong Gateway infrastructure using natural language. The system routes queries to specialized agents that interact with Kong Konnect via the MCP (Model Context Protocol) server, with real-time streaming responses via SSE.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [System Components](#system-components)
- [Prerequisites](#prerequisites)
- [Setup & Installation](#setup--installation)
- [Configuration](#configuration)
- [Running the Application](#running-the-application)
- [Running Tests](#running-tests)
- [API Reference](#api-reference)
- [Design Decisions](#design-decisions)
- [Trade-offs & Limitations](#trade-offs--limitations)
- [Known Issues & Areas for Improvement](#known-issues--areas-for-improvement)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                     API Layer                           │
│         FastAPI Chat Endpoint (SSE Streaming)           │
└─────────────────────────┬───────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────┐
│                   Routing Layer                         │
│              Multi-Agent Router                         │
│   ┌─────────────────────────────────────────────────┐   │
│   │  Tier 1: Rule-based  │  Tier 2: LLM Fallback   │   │
│   │  Keyword / Regex     │  Ollama (llama3.2:3b)   │   │
│   └─────────────────────────────────────────────────┘   │
└──────────────┬──────────────────────┬────────────────────┘
               │                      │
┌──────────────▼──────┐  ┌────────────▼──────────────────┐
│    Config Agent     │  │         Docs Agent             │
│ Live gateway data   │  │   Kong documentation search    │
│ via MCP tools       │  │   via KnowledgeBaseSearch      │
└──────────────┬──────┘  └────────────┬──────────────────┘
               └──────────┬───────────┘
┌─────────────────────────▼───────────────────────────────┐
│                  MCP Client Layer                       │
│     Session management · Tool discovery · Execution     │
│            Error handling · 30s timeout                 │
└─────────────────────────┬───────────────────────────────┘
                          │  POST /  (JSON-RPC 2.0)
                          │  mcp-session-id header
┌─────────────────────────▼───────────────────────────────┐
│            Kong Konnect MCP Server                      │
│         us.mcp.konghq.com · PAT Bearer Auth             │
└─────────────────────────────────────────────────────────┘
```

### Request Lifecycle

1. User sends a natural language query to the `/chat` endpoint
2. The router assembles query + conversation history
3. **Tier 1** — Rule-based routing scans for keywords/regex patterns (fast, deterministic)
4. If no match, **Tier 2** — LLM (Ollama) classifies intent using JSON structured output
5. The selected agent receives the query + full conversation history
6. Agent calls the appropriate MCP tool(s) on Kong Konnect
7. Structured response (summary + detail + raw data) streams back via SSE
8. Response is appended to in-memory conversation history for the next turn

---

## System Components

### Multi-Agent Router

Implements two-tier routing:

**Tier 1 — Rule-based (fast path)**
- Keyword and regex pattern matching
- Handles common queries instantly with zero LLM overhead
- Examples: queries containing `show`, `list`, `get`, `services`, `routes`, `plugins` → `config-agent`
- Examples: queries containing `how`, `configure`, `what is`, `best practice`, `setup` → `docs-agent`

**Tier 2 — LLM Fallback**
- Invoked only when Tier 1 finds no pattern match
- Sends query to local Ollama instance (`llama3.2:3b`)
- Uses JSON structured output mode to get agent selection + reasoning
- Routing method is always recorded in the response metadata

### Config Agent

Handles all live gateway configuration queries. Uses these MCP tools:

| Tool | Operations | Use case |
|------|-----------|----------|
| `GetControlPlane` | list, get_by_id, get_by_name, get_by_route | Entry point — resolves control plane ID |
| `GetService` | list, get_by_id, get_by_name | Upstream service configuration |
| `GetRoute` | list, get_by_id, get_by_name | Route path and method mappings |
| `GetPlugin` | list, get_by_id | Plugin configuration and enabled status |
| `GetConsumer` | list, get_by_id, get_by_name | API consumer management |
| `GetConsumerGroup` | list | Consumer group overview |
| `GetVault` | list, get_by_id, get_by_name | Secret vault integrations |
| `GetAnalytics` | query_api_requests, get_consumer_requests | Historical request analytics |

> **Note:** `GetControlPlane` is always called first since all other tools require `control_plane_id`.

### Docs Agent

Handles all documentation and how-to queries. Uses a single MCP tool:

| Tool | Required Params | Returns |
|------|----------------|---------|
| `KnowledgeBaseSearch` | `query` (string) | Plain text documentation excerpts, configuration examples, best practices |

### MCP Client

Manages the full MCP session lifecycle:

1. **Initialize** — POST to `https://us.mcp.konghq.com/` with `initialize` JSON-RPC method; captures `mcp-session-id` from response headers
2. **Tool discovery** — Fetches and caches available tool definitions on startup
3. **Tool execution** — Calls tools with proper parameters, 30-second timeout, PAT Bearer authentication
4. **Error handling** — Interprets tool errors, logs all calls, returns meaningful messages to users

### Chat API

- Built with FastAPI
- Streams responses using Server-Sent Events (SSE)
- Maintains conversation history in-memory (no database required)
- Each response includes: `summary`, `detailed_response`, `raw_data`, `routing_method`, `agent_used`

---

## Prerequisites

- Python 3.10+
- [Ollama](https://ollama.com/) installed and running
- A [Kong Konnect](https://konghq.com/products/kong-konnect/register) account with a Personal Access Token

---

## Setup & Installation

### 1. Clone the repository

```bash
git clone https://github.com/your-username/kong-agent-system.git
cd kong-agent-system
```

### 2. Create and activate a virtual environment

```bash
python -m venv kong-env
source kong-env/bin/activate   # macOS / Linux
# kong-env\Scripts\activate    # Windows
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Install and start Ollama

```bash
brew install ollama              # macOS
ollama serve &                   # start in background
ollama pull llama3.2:3b          # ~2GB download
```

Verify Ollama is running:
```bash
curl http://localhost:11434/v1/models
# Should return JSON with llama3.2:3b listed
```

### 5. Create a Kong Konnect account and Personal Access Token

1. Sign up at [https://konghq.com/products/kong-konnect/register](https://konghq.com/products/kong-konnect/register)
2. Navigate to **Account Settings → Personal Access Tokens**
3. Click **Generate Token**, name it (e.g. `kong-agent-dev`), select all read permissions
4. Copy the token immediately — it won't be shown again

---

## Configuration

Create a `.env` file at the project root (never commit this file):

```env
KONG_PAT=kpat_your_token_here
KONG_MCP_URL=https://us.mcp.konghq.com/
KONG_REGION=us
OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_MODEL=llama3.2:3b
```

**Region options:**
- US: `https://us.mcp.konghq.com/`
- EU: `https://eu.mcp.konghq.com/`
- AU: `https://au.mcp.konghq.com/`

To switch region, update both `KONG_MCP_URL` and `KONG_REGION` in `.env`.

---

## Running the Application

```bash
# Activate your virtual environment
source kong-env/bin/activate

# Make sure Ollama is running
ollama serve &

# Start the API server
uvicorn app.main:app --reload --port 8000
```

The API is now available at `http://localhost:8000`.

Interactive API docs: `http://localhost:8000/docs`

---

## Running Tests

```bash
pytest tests/ -v
```

Test coverage includes:
- **Routing logic** — verifies both rule-based patterns and LLM fallback produce correct agent selection
- **MCP tool calls** — verifies agents can successfully call Konnect MCP tools and handle responses
- **Multi-turn conversations** — verifies conversation history is correctly maintained across turns
- **Error handling** — verifies graceful degradation on auth failures, missing resources, timeouts

Run a specific test file:
```bash
pytest tests/test_router.py -v
pytest tests/test_agents.py -v
pytest tests/test_conversation.py -v
```

---

## API Reference

### `POST /chat`

Send a message and receive a streaming SSE response.

**Request body:**
```json
{
  "message": "Show me all services in my production control plane",
  "session_id": "optional-session-id-for-multi-turn"
}
```

**Response** — Server-Sent Events stream:
```
data: {"type": "routing", "agent": "config-agent", "method": "rule_based"}

data: {"type": "chunk", "content": "Found 3 services in your production control plane..."}

data: {"type": "done", "summary": "...", "raw_data": {...}, "routing_method": "rule_based", "agent_used": "config-agent"}
```

**Example queries:**

```bash
# Config agent — rule-based routing
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Show me all my services", "session_id": "test-1"}'

# Config agent — list routes
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "List all routes for the payments service", "session_id": "test-1"}'

# Docs agent — rule-based routing
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "How do I configure rate limiting?", "session_id": "test-2"}'

# Docs agent — best practices
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is the best way to add authentication?", "session_id": "test-2"}'

# Multi-turn — follow-up question in same session
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What plugins are enabled on it?", "session_id": "test-1"}'
```

### `GET /health`

Health check endpoint.

```bash
curl http://localhost:8000/health
# {"status": "ok", "ollama": "connected", "kong_mcp": "connected"}
```

### `DELETE /sessions/{session_id}`

Clear conversation history for a session.

```bash
curl -X DELETE http://localhost:8000/sessions/test-1
```

---

## Design Decisions

### Two-tier routing over pure LLM routing

A pure LLM router would be simpler to implement but adds ~500–1000ms latency to every query, even ones with obvious intent like "list services". Rule-based routing handles the common cases instantly and reserves the LLM for genuinely ambiguous queries. Routing method is always logged so the test suite can verify coverage.

### Local LLM (Ollama) over cloud APIs

Using `llama3.2:3b` via Ollama keeps costs at zero, ensures reproducibility across environments, and avoids network dependency for the routing step. The OpenAI-compatible API at `localhost:11434/v1` means the same client code works for both local and cloud models with a config change.

### Stateless agents with in-memory session history

Each agent call is stateless — the full conversation history is passed in on every request. This avoids database complexity while still supporting multi-turn context. The trade-off is that history is lost on server restart, which is acceptable for a development/demo system. The session store is a simple dict keyed by `session_id`.

### MCP session management in the client layer

The Konnect MCP server requires a session handshake (`initialize` → `mcp-session-id`) before any tool calls. Rather than each agent managing its own session, the MCP client handles session lifecycle centrally. Sessions are reused across tool calls within a request and refreshed on expiry.

### Structured agent responses

Every agent returns a consistent shape: `summary`, `detailed_response`, `raw_data`, `tool_calls_made`. This makes SSE streaming predictable, makes tests easy to write, and ensures the raw Kong data is always available to the caller alongside the LLM-generated summary.

### GetControlPlane as a mandatory first step in Config Agent

Every Config Agent MCP tool except `GetControlPlane` requires a `control_plane_id`. Rather than asking users to supply UUIDs, the agent always resolves the control plane by name first. This adds one extra MCP call but makes the system far more usable.

---

## Trade-offs & Limitations

| Area | Decision | Trade-off |
|------|----------|-----------|
| **Persistence** | In-memory session store | History lost on restart; production would need Redis or a DB |
| **LLM model** | `llama3.2:3b` local | Smaller model may misroute ambiguous queries vs GPT-4; fast and free |
| **MCP sessions** | New session per server start | Sessions expire; long-running servers need session refresh logic |
| **Authentication** | No API auth on chat endpoint | Fine for local dev; production needs API keys or OAuth |
| **Concurrency** | Single Ollama instance | Concurrent LLM fallback calls queue behind each other |
| **Error retry** | Single retry on MCP tool errors | Transient failures may surface as errors to user |
| **Analytics** | Basic request logging | No metrics, tracing, or dashboards |

---

## Known Issues & Areas for Improvement

- **Session expiry handling** — MCP sessions from Kong Konnect can expire mid-conversation. The current implementation detects this and re-initializes, but the failed tool call is not automatically retried.

- **Control plane disambiguation** — If a user has multiple control planes with similar names, the agent presents all matches and asks the user to clarify. This adds a turn of latency.

- **Ollama cold start** — The first LLM routing call after Ollama starts can take 3–5 seconds while the model loads into memory. Subsequent calls are fast.

- **Large tool responses** — `GetAnalytics` can return large payloads (up to 1000 requests). These are summarised by the agent but the full `raw_data` in the SSE response may be large.

- **No streaming from MCP tools** — MCP tool calls are blocking. The SSE stream starts only after the full tool response is received. True streaming would require MCP tool-level SSE support.

- **Rule-based routing coverage** — The current keyword list covers common cases. Edge cases (e.g. "tell me about my plugins") may fall through to LLM routing unnecessarily.

---

## Project Structure

```
kong-agent-system/
├── app/
│   ├── main.py              # FastAPI app, SSE endpoint
│   ├── settings.py          # Pydantic settings from .env
│   ├── router.py            # Two-tier multi-agent router
│   ├── mcp_client.py        # MCP session + tool execution
│   └── agents/
│       ├── base.py          # Shared agent interface
│       ├── config_agent.py  # Gateway config queries
│       └── docs_agent.py    # Documentation search
├── tests/
│   ├── test_router.py       # Routing logic tests
│   ├── test_agents.py       # Agent + MCP tool tests
│   └── test_conversation.py # Multi-turn context tests
├── .env                     # Local secrets (never committed)
├── .gitignore
├── requirements.txt
└── README.md
```

---

## Resources

- [Kong Konnect MCP Docs](https://developer.konghq.com/konnect-platform/konnect-mcp/)
- [MCP Tools Reference](https://developer.konghq.com/konnect-platform/konnect-mcp/tools/)
- [Ollama Documentation](https://ollama.com/)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [FastAPI Docs](https://fastapi.tiangolo.com/)



