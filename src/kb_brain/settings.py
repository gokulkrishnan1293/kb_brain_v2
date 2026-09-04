"""Runtime configuration.

All values are environment-driven (prefix ``KB_``) so the same image runs in
every environment. A ``.env`` file is read for local development only.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Model access: LiteLLM gateway (OpenAI-compatible) -------------------
    llm_base_url: str = Field(
        default="http://localhost:4000/v1",
        description="LiteLLM gateway base URL, including the /v1 suffix.",
    )
    llm_api_key: SecretStr = Field(
        default=SecretStr("sk-litellm-local"),
        description="LiteLLM virtual key. Scoped per team/service, not a provider key.",
    )
    llm_model: str = Field(
        default="claude-opus-5",
        description="Model alias as registered in the LiteLLM gateway config.",
    )
    llm_temperature: float = 0.0
    llm_timeout_seconds: float = 120.0
    llm_max_retries: int = 3

    # --- Configuration files -------------------------------------------------
    mcp_config_path: Path = REPO_ROOT / "config" / "mcp_servers.yaml"
    policy_config_path: Path = REPO_ROOT / "config" / "policies.yaml"

    # --- Storage -------------------------------------------------------------
    data_dir: Path = REPO_ROOT / "data"

    # --- Agent limits --------------------------------------------------------
    max_agent_steps: int = Field(
        default=12,
        description="Max reasoning/tool cycles per ingestion run before the graph halts.",
    )
    max_records_per_run: int = 2000

    # --- API -----------------------------------------------------------------
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    default_principal: str = Field(
        default="anonymous",
        description="Principal used when a request carries no identity header. "
        "Replace the header shim with real OIDC before any non-local deployment.",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
