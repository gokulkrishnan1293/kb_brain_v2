from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from kb_brain.governance import PolicyEngine
from kb_brain.mcp.connector import SourceConnector
from kb_brain.mcp.registry import MCPRegistry
from kb_brain.platform import KnowledgeBrain
from kb_brain.settings import Settings
from kb_brain.storage.filesystem import FilesystemRawStore

REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_CONFIG = Path(__file__).resolve().parent / "config"


@pytest.fixture(autouse=True, scope="session")
def _fixture_server_interpreter() -> None:
    """Spawn the fixture MCP server with the interpreter running the tests.

    A bare ``python3`` would resolve to the system interpreter, which has none
    of this project's dependencies installed.
    """
    os.environ.setdefault("KB_PYTHON", sys.executable)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        mcp_config_path=TEST_CONFIG / "mcp_servers.yaml",
        policy_config_path=TEST_CONFIG / "policies.yaml",
        data_dir=tmp_path / "data",
        max_agent_steps=4,
        max_records_per_run=50,
    )


@pytest.fixture
def registry(settings: Settings) -> MCPRegistry:
    return MCPRegistry.from_file(settings.mcp_config_path)


@pytest.fixture
def connector(registry: MCPRegistry) -> SourceConnector:
    return SourceConnector(registry)


@pytest.fixture
def policy(settings: Settings) -> PolicyEngine:
    return PolicyEngine.from_file(settings.policy_config_path)


@pytest.fixture
def store(settings: Settings) -> FilesystemRawStore:
    return FilesystemRawStore(settings.data_dir)


@pytest.fixture
def brain(settings: Settings) -> KnowledgeBrain:
    return KnowledgeBrain(settings)
