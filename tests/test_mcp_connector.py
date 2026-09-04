"""Live MCP connectivity against the in-repo fixture server.

These tests spawn a real MCP server over stdio and complete a real handshake --
the point is to prove the connection path works, not to mock it.
"""

from __future__ import annotations

import pytest

from kb_brain.errors import SourceUnavailable
from kb_brain.mcp.connector import SourceConnector


async def test_discovers_tools_over_stdio(connector: SourceConnector):
    names = {tool.name for tool in await connector.tools_for("jira")}
    assert {"jira_search", "jira_get_issue"} <= names


async def test_one_server_routes_tools_to_the_right_source(connector: SourceConnector):
    """The fixture server fronts both Jira and Confluence, as mcp-atlassian does."""
    jira = {tool.name for tool in await connector.tools_for("jira")}
    confluence = {tool.name for tool in await connector.tools_for("confluence")}
    assert jira and confluence
    assert not jira & confluence
    assert "confluence_get_page" in confluence


async def test_union_across_sources_covers_both(connector: SourceConnector):
    names = {tool.name for tool in await connector.tools_for_sources(["jira", "confluence"])}
    assert {"jira_search", "confluence_get_page"} <= names


async def test_write_tool_is_filtered_out(connector: SourceConnector):
    names = {tool.name for tool in await connector.tools_for("fixture")}
    assert "fixture_create_page" not in names


async def test_tool_actually_executes(connector: SourceConnector):
    tools = {tool.name: tool for tool in await connector.tools_for("jira")}
    result = await tools["jira_get_issue"].ainvoke({"issue_key": "ABC-101"})
    assert "Settlement batch fails" in str(result)


async def test_unknown_source_raises(connector: SourceConnector):
    with pytest.raises(SourceUnavailable, match="no MCP server configured"):
        await connector.tools_for("salesforce")


async def test_uncredentialed_source_raises(connector: SourceConnector):
    with pytest.raises(SourceUnavailable, match="no ready server"):
        await connector.tools_for("github")


async def test_status_report_covers_every_server(connector: SourceConnector):
    report = {entry["name"]: entry for entry in await connector.server_status()}
    assert report["fixture"]["status"] == "connected"
    assert report["fixture"]["tool_count"] >= 4
    assert report["needs_creds"]["status"] == "not_configured"
    assert report["needs_creds"]["missing_env"] == ["KB_TEST_ABSENT_TOKEN"]


def test_exception_group_is_unwrapped_for_operators():
    """Transport failures must name the real cause, not 'errors in a TaskGroup'."""
    from kb_brain.mcp.connector import describe_exception

    group = ExceptionGroup(
        "unhandled errors in a TaskGroup",
        [ConnectionRefusedError("connection refused to mcp host")],
    )
    described = describe_exception(group)
    assert "ConnectionRefusedError" in described
    assert "connection refused to mcp host" in described
    assert "TaskGroup" not in described
