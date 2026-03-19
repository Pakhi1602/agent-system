import re
import json
import logging
from openai import AsyncOpenAI
from app.models import AgentType, RoutingMethod, RoutingDecision, ConversationTurn
from app.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# Tier 1 — Rule-based patterns
# ---------------------------------------------------------------------------
# Each entry: (compiled regex, AgentType)
# Patterns are checked in order — first match wins.
# Keep config patterns before docs patterns to avoid false docs matches.

_CONFIG_PATTERNS: list[re.Pattern] = [re.compile(p, re.IGNORECASE) for p in [
    r"\b(list|show|get|fetch|display|find)\b.*(service|route|plugin|consumer|vault|control.?plane|analytics|request)",
    r"\b(service|route|plugin|consumer|vault|control.?plane)\b.*(list|show|get|all|configured|enabled|active)",
    r"\bwhat (services|routes|plugins|consumers|vaults|control.?planes)\b",
    r"\b(all|my|the) (services|routes|plugins|consumers|vaults)\b",
    r"\bplugins? (on|for|in|configured|enabled)\b",
    r"\broutes? (for|on|in|of)\b",
    r"\b(production|staging|dev).*(control.?plane|service|route)\b",
    r"\banalytics\b",
    r"\b(errors?|latency|requests?|traffic)\b.*(last|past|hour|day|week|minute)",
    r"\b(500|4\d\d)\b.*(error|request|response)",
]]

_DOCS_PATTERNS: list[re.Pattern] = [re.compile(p, re.IGNORECASE) for p in [
    r"\bhow (do|can|to|should)\b",
    r"\bwhat is\b",
    r"\bwhat('s| is) the (best|recommended|correct|right)\b",
    r"\b(configure|setup|set up|enable|install|add|integrate)\b.*(plugin|rate.?limit|auth|jwt|oauth|cors|acl|key)",
    r"\b(rate.?limit|authentication|authorization|jwt|oauth2?|cors|acl|basic.?auth|key.?auth)\b",
    r"\bbest (way|practice|approach)\b",
    r"\bdocumentation\b",
    r"\bexplain\b",
    r"\bguide\b",
    r"\btutorial\b",
    r"\bdifference between\b",
]]


def _rule_based_route(query: str) -> AgentType | None:
    """Return agent if a pattern matches, else None."""
    for pattern in _CONFIG_PATTERNS:
        if pattern.search(query):
            logger.debug("Rule match (config): %s", pattern.pattern)
            return AgentType.CONFIG

    for pattern in _DOCS_PATTERNS:
        if pattern.search(query):
            logger.debug("Rule match (docs): %s", pattern.pattern)
            return AgentType.DOCS

    return None


# ---------------------------------------------------------------------------
# Tier 2 — LLM fallback
# ---------------------------------------------------------------------------

_ROUTER_SYSTEM_PROMPT = """You are a routing assistant for a Kong Gateway management system.
Your job is to classify user queries and decide which agent should handle them.

Agents available:
- config-agent: handles queries about LIVE gateway data — listing/fetching services, routes,
  plugins, consumers, vaults, control planes, analytics, and request logs.
- docs-agent: handles queries about HOW TO DO THINGS — configuration guides, best practices,
  plugin documentation, conceptual explanations, and troubleshooting advice.

Respond ONLY with a valid JSON object in this exact format (no markdown, no extra text):
{
  "agent": "config-agent" or "docs-agent",
  "confidence": 0.0 to 1.0,
  "reasoning": "one sentence explaining why"
}"""


async def _llm_route(query: str, history: list[ConversationTurn]) -> RoutingDecision:
    """Ask Ollama to classify the query. Returns a RoutingDecision."""
    client = AsyncOpenAI(
        base_url=settings.ollama_base_url,
        api_key="ollama",           # Ollama ignores the key but client requires it
    )

    messages = [{"role": "system", "content": _ROUTER_SYSTEM_PROMPT}]

    # Include last 3 turns for context (avoids flooding the small model)
    for turn in history[-3:]:
        messages.append({"role": turn.role.value, "content": turn.content})

    messages.append({"role": "user", "content": query})

    try:
        response = await client.chat.completions.create(
            model=settings.ollama_model,
            messages=messages,
            temperature=settings.llm_router_temperature,
            max_tokens=settings.llm_router_max_tokens,
        )
        raw = response.choices[0].message.content.strip()
        logger.debug("LLM router raw response: %s", raw)

        parsed = json.loads(raw)
        agent = AgentType(parsed["agent"])
        confidence = float(parsed.get("confidence", 0.8))
        reasoning = parsed.get("reasoning", "")

        return RoutingDecision(
            agent=agent,
            method=RoutingMethod.LLM,
            confidence=confidence,
            reasoning=reasoning,
        )

    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning("LLM router parse error (%s) — defaulting to docs-agent", exc)
        return RoutingDecision(
            agent=AgentType.DOCS,
            method=RoutingMethod.LLM,
            confidence=0.5,
            reasoning=f"Parse error, safe default: {exc}",
        )
    except Exception as exc:
        logger.error("LLM router failed (%s) — defaulting to docs-agent", exc)
        return RoutingDecision(
            agent=AgentType.DOCS,
            method=RoutingMethod.LLM,
            confidence=0.5,
            reasoning=f"LLM error, safe default: {exc}",
        )


# ---------------------------------------------------------------------------
# Public router
# ---------------------------------------------------------------------------

async def route(
    query: str,
    history: list[ConversationTurn] | None = None,
) -> RoutingDecision:
    """
    Route a query to the appropriate agent.

    Tier 1: fast regex/keyword matching.
    Tier 2: LLM classification (only when Tier 1 has no match).
    """
    history = history or []

    # Tier 1
    agent = _rule_based_route(query)
    if agent is not None:
        logger.info("Routed via rule-based → %s", agent.value)
        return RoutingDecision(
            agent=agent,
            method=RoutingMethod.RULE_BASED,
            confidence=1.0,
            reasoning="Matched keyword/regex pattern",
        )

    # Tier 2
    logger.info("No rule match — falling back to LLM router")
    decision = await _llm_route(query, history)
    logger.info(
        "Routed via LLM → %s  (confidence=%.2f  reason=%s)",
        decision.agent.value,
        decision.confidence,
        decision.reasoning,
    )
    return decision