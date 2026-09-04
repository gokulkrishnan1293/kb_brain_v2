"""Connects the platform to source systems over MCP.

The connector is the only component that knows MCP exists. Everything above it
sees "tools for a source". That keeps the agent, the API and the SDK
independent of how a source is reached.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from ..errors import SourceUnavailable
from .registry import MCPRegistry, MCPServerSpec

logger = logging.getLogger(__name__)


def describe_exception(exc: BaseException, *, depth: int = 0) -> str:
    """Render an exception for an operator.

    Transport failures surface as ``ExceptionGroup`` from the underlying task
    group, whose own message ("unhandled errors in a TaskGroup") says nothing
    about what went wrong. Unwrap to the causes that actually explain it.
    """
    if isinstance(exc, BaseExceptionGroup) and depth < 3:
        inner = "; ".join(describe_exception(sub, depth=depth + 1) for sub in exc.exceptions)
        return inner or f"{type(exc).__name__}: {exc}"
    text = f"{type(exc).__name__}: {exc}".strip()
    if exc.__cause__ is not None and depth < 3:
        return f"{text} (caused by {describe_exception(exc.__cause__, depth=depth + 1)})"
    return text


class SourceConnector:
    """Discovers and exposes read tools from the configured MCP servers."""

    def __init__(self, registry: MCPRegistry) -> None:
        self._registry = registry
        self._tool_cache: dict[str, list[BaseTool]] = {}

    @property
    def registry(self) -> MCPRegistry:
        return self._registry

    # -- discovery ------------------------------------------------------------

    async def server_status(self) -> list[dict[str, Any]]:
        """Health/readiness of every configured server, for the console and API.

        Reports rather than raises: one misconfigured source must not take the
        platform down.
        """
        report: list[dict[str, Any]] = []
        for server in self._registry.all():
            entry: dict[str, Any] = {
                "name": server.name,
                "sources": server.source_names,
                "transport": server.transport,
                "enabled": server.enabled,
                "ready": server.ready,
                "missing_env": server.missing_env,
                "description": server.description,
            }
            if server.ready:
                try:
                    tools = await self._load_server_tools(server)
                    entry["tool_count"] = len(tools)
                    entry["tools"] = [tool.name for tool in tools]
                    entry["status"] = "connected"
                except Exception as exc:  # noqa: BLE001 - surfaced, not swallowed
                    entry["status"] = "error"
                    entry["error"] = describe_exception(exc)
            else:
                entry["status"] = "not_configured"
            report.append(entry)
        return report

    async def tools_for(self, source: str) -> list[BaseTool]:
        """All permitted tools that can serve ``source``, across every server."""
        servers = self._registry.servers_for(source)
        if not servers:
            known = ", ".join(self._registry.sources()) or "<none>"
            raise SourceUnavailable(
                f"no MCP server configured for source '{source}'; known: {known}"
            )

        ready = [server for server in servers if server.ready]
        if not ready:
            details = "; ".join(
                f"{server.name} missing env {server.missing_env}" for server in servers
            )
            raise SourceUnavailable(f"source '{source}' has no ready server: {details}")

        collected: list[BaseTool] = []
        failures: list[str] = []
        for server in ready:
            try:
                # A server may front several sources; take only the tools that
                # belong to this one so provenance names the true origin.
                collected.extend(
                    tool
                    for tool in await self._load_server_tools(server)
                    if server.serves(tool.name, source)
                )
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{server.name}: {describe_exception(exc)}")

        if not collected:
            raise SourceUnavailable(
                f"source '{source}' exposed no usable tools. " + ("; ".join(failures) or "")
            )
        if failures:
            logger.warning("Partial tool discovery for source %s: %s", source, "; ".join(failures))
        return collected

    async def tools_for_sources(self, sources: list[str]) -> list[BaseTool]:
        """Union of tools across several sources, de-duplicated by tool name."""
        seen: dict[str, BaseTool] = {}
        for source in sources:
            for tool in await self.tools_for(source):
                seen.setdefault(tool.name, tool)
        return list(seen.values())

    # -- internals ------------------------------------------------------------

    async def _load_server_tools(self, server: MCPServerSpec) -> list[BaseTool]:
        if server.name in self._tool_cache:
            return self._tool_cache[server.name]

        client = MultiServerMCPClient({server.name: server.to_connection()})
        tools = await client.get_tools(server_name=server.name)

        permitted = [tool for tool in tools if server.tools.permits(tool.name)]
        blocked = len(tools) - len(permitted)
        if blocked:
            logger.info(
                "Server %s: %d/%d tools blocked by tool filter", server.name, blocked, len(tools)
            )
        self._tool_cache[server.name] = permitted
        return permitted

    def invalidate_cache(self) -> None:
        self._tool_cache.clear()
