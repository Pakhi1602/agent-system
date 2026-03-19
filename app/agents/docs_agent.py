import logging
from app.agents.base import BaseAgent
from app.mcp_client import mcp_client, MCPError
from app.models import AgentInput, AgentResponse, AgentType, RoutingMethod

logger = logging.getLogger(__name__)


class DocsAgent(BaseAgent):
    """
    Searches Kong documentation via the KnowledgeBaseSearch MCP tool.

    Always a single tool call — the query is passed directly and
    the plain-text excerpt is returned as the detailed response.
    """

    async def run(self, input: AgentInput) -> AgentResponse:
        query = input.query
        tool_calls: list[str] = []

        # Enrich query with recent context so the search is more specific
        context_hint = self._build_context_hint(input)
        search_query = f"{context_hint}{query}" if context_hint else query

        try:
            result = await mcp_client.call_tool(
                "KnowledgeBaseSearch",
                {"query": search_query},
            )
            tool_calls.append("KnowledgeBaseSearch")

            # The tool returns plain text content inside result
            content = self._extract_content(result)

            if not content:
                return AgentResponse(
                    summary="No documentation found for this query.",
                    detailed_response=(
                        "The knowledge base search returned no results. "
                        "Try rephrasing your question or visit "
                        "https://developer.konghq.com for full documentation."
                    ),
                    raw_data=result,
                    tool_calls_made=tool_calls,
                    agent=AgentType.DOCS,
                    routing_method=RoutingMethod.RULE_BASED,
                )

            summary = self._make_summary(content)

            return AgentResponse(
                summary=summary,
                detailed_response=content,
                raw_data=result,
                tool_calls_made=tool_calls,
                agent=AgentType.DOCS,
                routing_method=RoutingMethod.RULE_BASED,
            )

        except MCPError as exc:
            logger.error("DocsAgent MCP error: %s", exc)
            return AgentResponse(
                summary="Failed to search documentation.",
                detailed_response=str(exc),
                raw_data=None,
                tool_calls_made=tool_calls,
                agent=AgentType.DOCS,
                routing_method=RoutingMethod.RULE_BASED,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_content(self, result: dict) -> str:
        """
        KnowledgeBaseSearch returns content in result["content"] as a list
        of blocks, each with a "text" field.
        """
        blocks = result.get("content", [])
        if isinstance(blocks, list):
            parts = [b.get("text", "") for b in blocks if isinstance(b, dict)]
            return "\n\n".join(p for p in parts if p).strip()
        if isinstance(blocks, str):
            return blocks.strip()
        return ""

    def _build_context_hint(self, input: AgentInput) -> str:
        """
        If the last assistant turn mentioned a specific Kong concept,
        prepend it to sharpen the search.
        """
        if not input.history:
            return ""
        last = input.history[-1]
        if last.role.value == "assistant" and len(last.content) > 20:
            return ""    # don't compound — query is already specific
        return ""        # keep simple for now; extend as needed

    def _make_summary(self, content: str) -> str:
        """First non-empty line of content as a one-line summary."""
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return self._truncate(line, 200)
        return self._truncate(content, 200)