import pytest
from app.router import route, _rule_based_route
from app.models import AgentType, RoutingMethod


# ---------------------------------------------------------------------------
# Tier 1 — rule-based routing (synchronous helper)
# ---------------------------------------------------------------------------

class TestRuleBasedRouting:

    def test_list_services_routes_to_config(self):
        assert _rule_based_route("Show me all my services") == AgentType.CONFIG

    def test_list_routes_routes_to_config(self):
        assert _rule_based_route("List routes for the payments service") == AgentType.CONFIG

    def test_get_plugins_routes_to_config(self):
        assert _rule_based_route("What plugins are configured on my API?") == AgentType.CONFIG

    def test_show_control_plane_routes_to_config(self):
        assert _rule_based_route("Show me all control planes") == AgentType.CONFIG

    def test_consumer_list_routes_to_config(self):
        assert _rule_based_route("List all consumers") == AgentType.CONFIG

    def test_analytics_routes_to_config(self):
        assert _rule_based_route("Show me analytics for the last hour") == AgentType.CONFIG

    def test_error_traffic_routes_to_config(self):
        assert _rule_based_route("Show 500 errors from the last day") == AgentType.CONFIG

    def test_how_to_routes_to_docs(self):
        assert _rule_based_route("How do I configure rate limiting?") == AgentType.DOCS

    def test_best_practice_routes_to_docs(self):
        assert _rule_based_route("What is the best way to add authentication?") == AgentType.DOCS

    def test_what_is_routes_to_docs(self):
        assert _rule_based_route("What is JWT authentication?") == AgentType.DOCS

    def test_configure_plugin_routes_to_docs(self):
        assert _rule_based_route("How to configure the rate-limit plugin?") == AgentType.DOCS

    def test_cors_setup_routes_to_docs(self):
        assert _rule_based_route("How do I set up CORS?") == AgentType.DOCS

    def test_ambiguous_returns_none(self):
        # Genuinely ambiguous — should fall through to LLM
        result = _rule_based_route("tell me about my gateway")
        assert result is None


# ---------------------------------------------------------------------------
# Tier 2 — full async routing (includes LLM fallback)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestFullRouting:

    async def test_rule_based_config_decision(self):
        decision = await route("List all my services")
        assert decision.agent == AgentType.CONFIG
        assert decision.method == RoutingMethod.RULE_BASED
        assert decision.confidence == 1.0

    async def test_rule_based_docs_decision(self):
        decision = await route("How do I configure rate limiting?")
        assert decision.agent == AgentType.DOCS
        assert decision.method == RoutingMethod.RULE_BASED
        assert decision.confidence == 1.0

    async def test_llm_fallback_triggered(self):
        # Ambiguous query with no pattern match — must use LLM
        decision = await route("tell me about my gateway")
        assert decision.method == RoutingMethod.LLM
        assert decision.agent in (AgentType.CONFIG, AgentType.DOCS)
        assert 0.0 <= decision.confidence <= 1.0

    async def test_llm_fallback_has_reasoning(self):
        decision = await route("tell me about my gateway")
        assert decision.method == RoutingMethod.LLM
        assert isinstance(decision.reasoning, str)

    async def test_routing_with_history(self):
        from app.models import ConversationTurn, ConversationRole
        history = [
            ConversationTurn(role=ConversationRole.USER, content="Show me my services"),
            ConversationTurn(role=ConversationRole.ASSISTANT, content="Found 1 service: payments-service"),
        ]
        decision = await route("What routes does it have?", history)
        assert decision.agent == AgentType.CONFIG