"""Model access through the LiteLLM gateway.

All model traffic leaves the platform through one gateway so keys, budgets,
rate limits and per-team spend are governed centrally. Callers never see a
provider credential -- they see a LiteLLM virtual key.

The gateway speaks the OpenAI-compatible protocol, so the OpenAI SDK client
(via ``langchain-openai``) is the transport. Swapping in a native provider SDK
later means replacing this module only; nothing above it imports a model class.
"""

from __future__ import annotations

from typing import Protocol

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from ..settings import Settings, get_settings


class ModelGateway(Protocol):
    """The contract the rest of the platform depends on."""

    def chat_model(self, *, tags: list[str] | None = None) -> BaseChatModel: ...


def build_chat_model(
    settings: Settings | None = None,
    *,
    model: str | None = None,
    tags: list[str] | None = None,
) -> BaseChatModel:
    """Construct a chat model bound to the LiteLLM gateway.

    ``tags`` are forwarded as LiteLLM request tags so spend and traces can be
    attributed to a principal and a scope rather than to "the platform".
    """
    settings = settings or get_settings()
    headers: dict[str, str] = {}
    if tags:
        headers["x-litellm-tags"] = ",".join(tags)

    return ChatOpenAI(
        model=model or settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        default_headers=headers or None,
    )


class LiteLLMGateway:
    """Default :class:`ModelGateway` implementation."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def chat_model(self, *, tags: list[str] | None = None) -> BaseChatModel:
        return build_chat_model(self._settings, tags=tags)
