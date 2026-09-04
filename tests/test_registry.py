from __future__ import annotations

import pytest

from kb_brain.mcp.registry import MCPRegistry, ToolFilter


def test_lists_sources_across_servers(registry: MCPRegistry):
    assert registry.sources() == ["confluence", "fixture", "github", "jira"]


def test_server_lookup_by_source(registry: MCPRegistry):
    assert [server.name for server in registry.servers_for("jira")] == ["fixture"]
    assert registry.servers_for("nothing-here") == []


def test_missing_credentials_mark_server_unready(registry: MCPRegistry):
    server = registry.get("needs_creds")
    assert server.missing_env == ["KB_TEST_ABSENT_TOKEN"]
    assert not server.ready


def test_stdio_env_drops_blank_expansions(registry: MCPRegistry, monkeypatch):
    monkeypatch.setenv("PRESET", "keep-me")
    server = registry.get("fixture")
    connection = server.to_connection()
    assert connection["transport"] == "stdio"
    assert connection["env"]["PRESET"] == "keep-me"


@pytest.mark.parametrize(
    ("tool", "expected"),
    [
        ("jira_search", True),
        ("confluence_get_page", True),
        ("fixture_create_page", False),
        ("some_other_tool", False),
    ],
)
def test_tool_filter(registry: MCPRegistry, tool: str, expected: bool):
    assert registry.get("fixture").tools.permits(tool) is expected


def test_deny_beats_allow():
    filter_ = ToolFilter(allow=["*"], deny=["*_delete_*"])
    assert filter_.permits("get_page")
    assert not filter_.permits("page_delete_all")
