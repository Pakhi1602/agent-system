from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Kong Konnect MCP
    kong_pat: str
    kong_mcp_url: str = "https://us.mcp.konghq.com/"
    kong_region: str = "us"

    # Ollama
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "llama3.2:3b"

    # MCP client behaviour
    mcp_tool_timeout: int = 30       # seconds per tool call
    mcp_session_ttl: int = 3600      # seconds before re-initialising session

    # Router
    llm_router_temperature: float = 0.0
    llm_router_max_tokens: int = 256

    # App
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()