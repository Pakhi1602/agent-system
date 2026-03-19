from pydantic import BaseModel, Field
from typing import Any, Literal
from enum import Enum


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

class AgentType(str, Enum):
    CONFIG = "config-agent"
    DOCS   = "docs-agent"


class RoutingMethod(str, Enum):
    RULE_BASED = "rule_based"
    LLM        = "llm_fallback"


class RoutingDecision(BaseModel):
    agent: AgentType
    method: RoutingMethod
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    reasoning: str = ""


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------

class ConversationRole(str, Enum):
    USER      = "user"
    ASSISTANT = "assistant"


class ConversationTurn(BaseModel):
    role: ConversationRole
    content: str


# ---------------------------------------------------------------------------
# Agent I/O
# ---------------------------------------------------------------------------

class AgentInput(BaseModel):
    query: str
    history: list[ConversationTurn] = Field(default_factory=list)
    session_id: str = ""


class AgentResponse(BaseModel):
    summary: str
    detailed_response: str
    raw_data: dict[str, Any] | None = None
    tool_calls_made: list[str] = Field(default_factory=list)
    agent: AgentType
    routing_method: RoutingMethod
    error: str | None = None


# ---------------------------------------------------------------------------
# Chat API
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str
    session_id: str = Field(default="", description="Omit to start a new session")


class SSEEvent(BaseModel):
    type: Literal["routing", "chunk", "done", "error"]
    data: dict[str, Any] = Field(default_factory=dict)