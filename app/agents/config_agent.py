import re
import json
import logging
from typing import Any
from openai import AsyncOpenAI
from app.agents.base import BaseAgent
from app.mcp_client import mcp_client, MCPError
from app.models import AgentInput, AgentResponse, AgentType, RoutingMethod
from app.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# Tool selection prompt
# ---------------------------------------------------------------------------

_TOOL_SELECTION_PROMPT = """You are a Kong Gateway configuration assistant.
Given a user query and the list of available MCP tools, select the right tool and parameters.

Available tools:
- GetControlPlane: operations = list | get_by_id | get_by_name | get_by_route
- GetService: operations = list | get_by_id | get_by_name  (requires control_plane_id)
- GetRoute: operations = list | get_by_id | get_by_name  (requires control_plane_id)
- GetPlugin: operations = list | get_by_id  (requires control_plane_id)
- GetConsumer: operations = list | get_by_id | get_by_name  (requires control_plane_id)
- GetConsumerGroup: operations = list  (requires control_plane_id)
- GetVault: operations = list | get_by_id | get_by_name  (requires control_plane_id)
- GetAnalytics: operations = query_api_requests | get_consumer_requests  (requires time_range)

Rules:
- ALWAYS use GetControlPlane first if control_plane_id is not already known.
- If the user mentions a name (e.g. "payments service"), use get_by_name operation.
- For analytics queries include time_range: one of 15M, 1H, 6H, 12H, 24H, 7D.

Respond ONLY with a valid JSON object (no markdown):
{
  "tool": "<tool_name>",
  "params": { ... }
}"""


class ConfigAgent(BaseAgent):
    """
    Handles live gateway configuration queries.

    Strategy:
        1. Always resolve the control plane first (GetControlPlane list).
        2. Use LLM to select the right follow-up tool + params.
        3. Call that tool and return structured response.
    """

    def __init__(self):
        self._llm = AsyncOpenAI(
            base_url=settings.ollama_base_url,
            api_key="ollama",
        )

    async def run(self, input: AgentInput) -> AgentResponse:
        tool_calls: list[str] = []
        raw_data: dict[str, Any] = {}

        try:
            # Step 1 — resolve control plane
            cp_result = await mcp_client.call_tool(
                "GetControlPlane", {"operation": "list"}
            )
            tool_calls.append("GetControlPlane")
            control_planes = self._extract_list(cp_result)
            raw_data["control_planes"] = control_planes

            if not control_planes:
                return self._no_data_response(
                    "No control planes found in your Kong Konnect account.",
                    tool_calls, raw_data,
                )

            # Use the first control plane by default
            # (ask user to disambiguate only if they explicitly name one)
            control_plane = self._pick_control_plane(control_planes, input.query)
            control_plane_id = control_plane.get("id", "")
            cp_name = control_plane.get("name", control_plane_id)

            if not control_plane_id:
                return self._no_data_response(
                    "Could not determine control plane ID.",
                    tool_calls, raw_data,
                )

            # Step 2 — LLM selects the follow-up tool
            tool_call = await self._select_tool(input, control_plane_id)
            if not tool_call:
                return self._no_data_response(
                    "Could not determine which tool to use for this query.",
                    tool_calls, raw_data,
                )

            tool_name = tool_call.get("tool", "")
            tool_params = tool_call.get("params", {})

            # Guarantee required params are always present
            tool_params = self._enforce_params(tool_name, tool_params, control_plane_id)

            # Step 3 — execute the selected tool
            logger.info("ConfigAgent executing %s with %s", tool_name, tool_params)
            result = await mcp_client.call_tool(tool_name, tool_params)
            tool_calls.append(tool_name)
            raw_data["result"] = result

            # Step 4 — build human-readable response
            items = self._extract_list(result)
            summary = self._build_summary(tool_name, items, cp_name, tool_params)
            detailed = self._build_detailed(tool_name, items, result)

            return AgentResponse(
                summary=summary,
                detailed_response=detailed,
                raw_data=raw_data,
                tool_calls_made=tool_calls,
                agent=AgentType.CONFIG,
                routing_method=RoutingMethod.RULE_BASED,
            )

        except MCPError as exc:
            logger.error("ConfigAgent MCP error: %s", exc)
            return AgentResponse(
                summary="Failed to fetch gateway configuration.",
                detailed_response=self._friendly_error(exc),
                raw_data=raw_data,
                tool_calls_made=tool_calls,
                agent=AgentType.CONFIG,
                routing_method=RoutingMethod.RULE_BASED,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # LLM tool selection
    # ------------------------------------------------------------------

    async def _select_tool(
        self, input: AgentInput, control_plane_id: str
    ) -> dict | None:
        """Ask Ollama to pick the right MCP tool + params for this query."""
        history_text = "\n".join(
            f"{t.role.value}: {t.content}" for t in input.history[-4:]
        )
        user_message = (
            f"Conversation so far:\n{history_text}\n\n"
            f"Current query: {input.query}\n"
            f"Known control_plane_id: {control_plane_id}\n\n"
            "Select the tool and parameters."
        )

        try:
            response = await self._llm.chat.completions.create(
                model=settings.ollama_model,
                messages=[
                    {"role": "system", "content": _TOOL_SELECTION_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.0,
                max_tokens=300,
            )
            raw = response.choices[0].message.content.strip()
            logger.debug("Tool selection LLM response: %s", raw)

            # Strip markdown fences if model wraps in ```json
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

            return json.loads(raw)

        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning("Tool selection parse error: %s", exc)
            # Sensible fallback — just list services
            return {
                "tool": "GetService",
                "params": {"operation": "list", "control_plane_id": control_plane_id},
            }
        except Exception as exc:
            logger.error("Tool selection LLM failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    # Required params and their defaults for every tool
    _TOOL_DEFAULTS: dict[str, dict] = {
        "GetControlPlane":      {"operation": "list"},
        "GetService":           {"operation": "list"},
        "GetRoute":             {"operation": "list"},
        "GetPlugin":            {"operation": "list"},
        "GetConsumer":          {"operation": "list"},
        "GetConsumerGroup":     {"operation": "list"},
        "GetVault":             {"operation": "list"},
        "GetAnalytics":         {"operation": "query_api_requests", "time_range": "1H"},
        "GetControlPlaneGroup": {"operation": "list"},
    }

    def _enforce_params(
        self, tool_name: str, params: dict, control_plane_id: str
    ) -> dict:
        """Guarantee every required param is present, filling defaults where needed."""
        result = dict(params)

        # Always inject operation if missing
        defaults = self._TOOL_DEFAULTS.get(tool_name, {})
        for key, default_val in defaults.items():
            if key not in result or not result[key]:
                logger.warning(
                    "Tool %s missing param '%s' — injecting default '%s'",
                    tool_name, key, default_val,
                )
                result[key] = default_val

        # Always inject control_plane_id for tools that need it
        if tool_name != "GetControlPlane" and "control_plane_id" not in result:
            result["control_plane_id"] = control_plane_id

        logger.debug("Enforced params for %s: %s", tool_name, result)
        return result

    def _extract_list(self, result: dict) -> list[dict]:
        """Pull the first list found in an MCP result dict."""
        content = result.get("content", [])
        # Content blocks are usually [{"type": "text", "text": "<json string>"}]
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    try:
                        parsed = json.loads(block["text"])
                        if isinstance(parsed, list):
                            return parsed
                        if isinstance(parsed, dict):
                            # Unwrap common envelope shapes
                            for key in ("data", "items", "results", "services",
                                        "routes", "plugins", "consumers",
                                        "vaults", "control_planes"):
                                if key in parsed and isinstance(parsed[key], list):
                                    return parsed[key]
                            return [parsed]
                    except (json.JSONDecodeError, TypeError):
                        pass
        return []

    def _pick_control_plane(
        self, control_planes: list[dict], query: str
    ) -> dict:
        """
        If the query mentions a control plane name, try to match it.
        Otherwise return the first one.
        """
        query_lower = query.lower()
        for cp in control_planes:
            name = cp.get("name", "").lower()
            if name and name in query_lower:
                return cp
        return control_planes[0]

    def _build_summary(
        self,
        tool_name: str,
        items: list[dict],
        cp_name: str,
        params: dict,
    ) -> str:
        count = len(items)
        operation = params.get("operation", "list")
        entity = tool_name.replace("Get", "").lower()

        if count == 0:
            return f"No {entity}s found in control plane '{cp_name}'."
        if operation in ("get_by_id", "get_by_name") and count == 1:
            name = items[0].get("name") or items[0].get("id", "unknown")
            return f"Found {entity} '{name}' in control plane '{cp_name}'."
        return f"Found {count} {entity}(s) in control plane '{cp_name}'."

    def _build_detailed(
        self, tool_name: str, items: list[dict], raw_result: dict
    ) -> str:
        if not items:
            content = raw_result.get("content", [])
            if content:
                return self._extract_text_blocks(raw_result)
            return "No data returned."

        entity = tool_name.replace("Get", "")
        lines = [f"{entity} details:\n"]
        for i, item in enumerate(items, 1):
            name = item.get("name") or item.get("username") or item.get("id", f"item-{i}")
            lines.append(f"{i}. {name}")
            # Show a few key fields without flooding the response
            for field in ("id", "host", "port", "protocol", "enabled",
                          "path", "methods", "tags"):
                if field in item and item[field] is not None:
                    lines.append(f"   {field}: {item[field]}")
        return "\n".join(lines)

    def _extract_text_blocks(self, result: dict) -> str:
        blocks = result.get("content", [])
        parts = []
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block["text"])
        return "\n\n".join(parts)

    def _no_data_response(
        self, message: str, tool_calls: list[str], raw_data: dict
    ) -> AgentResponse:
        return AgentResponse(
            summary=message,
            detailed_response=message,
            raw_data=raw_data,
            tool_calls_made=tool_calls,
            agent=AgentType.CONFIG,
            routing_method=RoutingMethod.RULE_BASED,
        )

    def _friendly_error(self, exc: MCPError) -> str:
        msg = str(exc)
        if exc.code == 401:
            return "Authentication failed. Check your KONG_PAT in .env."
        if exc.code == 404:
            return "The requested resource was not found in Kong Konnect."
        if "timed out" in msg:
            return "The request to Kong Konnect timed out. Try again in a moment."
        return f"Kong Konnect error: {msg}"