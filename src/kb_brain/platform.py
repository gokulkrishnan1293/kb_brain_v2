"""Composition root.

One object that assembles the platform's components from configuration. Every
interface (API, CLI, future MCP server) builds the same object, which is what
keeps "the brain owns the logic; consumers only consume" true in practice.
"""

from __future__ import annotations

from functools import lru_cache

from .agents.ingestion import IngestionAgent
from .governance import PolicyEngine
from .mcp.connector import SourceConnector
from .mcp.registry import MCPRegistry
from .settings import Settings, get_settings
from .storage.filesystem import FilesystemRawStore


class KnowledgeBrain:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.registry = MCPRegistry.from_file(self.settings.mcp_config_path)
        self.connector = SourceConnector(self.registry)
        self.policy = PolicyEngine.from_file(self.settings.policy_config_path)
        self.store = FilesystemRawStore(self.settings.data_dir)
        self.ingestion = IngestionAgent(
            connector=self.connector,
            policy=self.policy,
            store=self.store,
            settings=self.settings,
        )


@lru_cache(maxsize=1)
def get_brain() -> KnowledgeBrain:
    return KnowledgeBrain()
