from abc import ABC, abstractmethod
from app.models import AgentInput, AgentResponse


class BaseAgent(ABC):
    """
    All agents implement this interface.

    Agents receive an AgentInput (query + full conversation history)
    and return a structured AgentResponse.
    """

    @abstractmethod
    async def run(self, input: AgentInput) -> AgentResponse:
        """Execute the agent and return a structured response."""
        ...

    def _truncate(self, text: str, max_chars: int = 2000) -> str:
        """Safely truncate long strings for summaries."""
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + f"... [truncated {len(text) - max_chars} chars]"