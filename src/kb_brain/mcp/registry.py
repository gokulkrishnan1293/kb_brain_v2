"""Declarative registry of MCP servers.

Sources are configuration, not code. Adding Confluence, Jira, GitHub or a
future enterprise system is a YAML entry -- the ingestion agent needs no
change, because it discovers capabilities from the servers at runtime.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from ..errors import ConfigError

# ${VAR} or ${VAR:-fallback}
_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class ToolFilter(BaseModel):
    """Which of a server's tools this platform is willing to expose.

    Servers advertise write tools too (create issue, update page). The brain is
    the governance boundary, so ingestion only ever sees an explicit read
    allowlist.
    """

    allow: list[str] = Field(default_factory=lambda: ["*"])
    deny: list[str] = Field(default_factory=list)

    def permits(self, tool_name: str) -> bool:
        from fnmatch import fnmatch

        if any(fnmatch(tool_name, pattern) for pattern in self.deny):
            return False
        return any(fnmatch(tool_name, pattern) for pattern in self.allow)


class MCPServerSpec(BaseModel):
    """One MCP server and the logical sources it serves.

    A single server often fronts several sources (mcp-atlassian serves both
    Jira and Confluence). ``sources`` therefore maps each logical source to the
    tool patterns that belong to it, so a harvested record can name its true
    origin. The shorthand list form maps every tool to every listed source::

        sources: [github]                      # all tools serve 'github'
        sources:                               # tools split by source
          jira: ["jira_*"]
          confluence: ["confluence_*"]
    """

    name: str
    sources: dict[str, list[str]] = Field(
        min_length=1,
        description="Logical source -> tool name patterns served for it.",
    )

    @field_validator("sources", mode="before")
    @classmethod
    def _normalize_sources(cls, value: Any) -> Any:
        if isinstance(value, list):
            return {str(source): ["*"] for source in value}
        return value
    transport: Literal["stdio", "streamable_http", "sse"] = "stdio"
    enabled: bool = True
    description: str = ""

    # stdio
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None

    # http transports
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)

    tools: ToolFilter = Field(default_factory=ToolFilter)
    # Env vars that must be present for the server to be usable.
    requires_env: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_transport_fields(self) -> MCPServerSpec:
        if self.transport == "stdio" and not self.command:
            raise ValueError(f"server '{self.name}': stdio transport requires 'command'")
        if self.transport in {"streamable_http", "sse"} and not self.url:
            raise ValueError(f"server '{self.name}': {self.transport} transport requires 'url'")
        return self

    @property
    def missing_env(self) -> list[str]:
        return [var for var in self.requires_env if not os.environ.get(var)]

    @property
    def ready(self) -> bool:
        """Configured *and* credentialed. Unready servers are reported, not fatal."""
        return self.enabled and not self.missing_env

    @property
    def source_names(self) -> list[str]:
        return list(self.sources)

    def serves(self, tool_name: str, source: str) -> bool:
        """Whether this tool belongs to the given logical source."""
        from fnmatch import fnmatch

        patterns = self.sources.get(source)
        if patterns is None:
            return False
        return any(fnmatch(tool_name, pattern) for pattern in patterns)

    def to_connection(self) -> dict[str, Any]:
        """Render the connection dict expected by MultiServerMCPClient."""
        if self.transport == "stdio":
            # Unset ${VARS} expand to "", which would shadow a real value the
            # child could otherwise inherit -- drop them instead of passing blanks.
            overrides = {key: value for key, value in self.env.items() if value != ""}
            return {
                "transport": "stdio",
                "command": self.command,
                "args": list(self.args),
                # Merge with the parent environment so servers can still read
                # PATH-adjacent config (certs, proxies, tool caches).
                "env": {**os.environ, **overrides},
                **({"cwd": self.cwd} if self.cwd else {}),
            }
        return {
            "transport": self.transport,
            "url": self.url,
            **({"headers": self.headers} if self.headers else {}),
        }


def _expand(value: Any) -> Any:
    """Recursively substitute ``${VAR}`` / ``${VAR:-default}`` from the environment."""
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            var, fallback = match.group(1), match.group(2)
            return os.environ.get(var, fallback if fallback is not None else "")

        return _ENV_REF.sub(replace, value)
    if isinstance(value, dict):
        return {key: _expand(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand(item) for item in value]
    return value


class MCPRegistry:
    """Loads and queries the MCP server catalogue."""

    def __init__(self, servers: list[MCPServerSpec]) -> None:
        self._servers = {server.name: server for server in servers}

    @classmethod
    def from_file(cls, path: Path) -> MCPRegistry:
        if not path.exists():
            raise ConfigError(f"MCP config not found: {path}")
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"invalid YAML in {path}: {exc}") from exc

        entries = raw.get("servers") or {}
        if not isinstance(entries, dict):
            raise ConfigError(f"{path}: 'servers' must be a mapping of name -> server spec")

        servers: list[MCPServerSpec] = []
        for name, spec in entries.items():
            if not isinstance(spec, dict):
                raise ConfigError(f"{path}: server '{name}' must be a mapping")
            # Env values are expanded lazily at load time so a rotated secret
            # only needs a restart, not a config edit.
            payload = {**_expand(spec), "name": name}
            try:
                servers.append(MCPServerSpec.model_validate(payload))
            except ValueError as exc:
                raise ConfigError(f"{path}: invalid server '{name}': {exc}") from exc
        return cls(servers)

    def all(self) -> list[MCPServerSpec]:
        return list(self._servers.values())

    def get(self, name: str) -> MCPServerSpec:
        try:
            return self._servers[name]
        except KeyError:
            known = ", ".join(sorted(self._servers)) or "<none>"
            raise ConfigError(f"unknown MCP server '{name}'; configured: {known}") from None

    def sources(self) -> list[str]:
        return sorted(
            {source for server in self._servers.values() for source in server.source_names}
        )

    def servers_for(self, source: str) -> list[MCPServerSpec]:
        """Every enabled server that can serve the given logical source."""
        return [
            server
            for server in self._servers.values()
            if server.enabled and source in server.sources
        ]
