"""Deterministic MCP fixture server.

Stands in for Jira, Confluence and GitHub so the ingestion harness can be
exercised end-to-end -- real stdio transport, real MCP handshake, real tool
schemas -- with no enterprise credentials.

Run directly: ``python -m tests.fake_mcp_server``
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("kb-fixture")

_ISSUES = [
    {
        "key": "ABC-101",
        "summary": "Settlement batch fails when currency is missing",
        "status": "Closed",
        "type": "Bug",
        "resolution": "Fixed",
        "description": "Batch job aborted because the FX lookup returned null for legacy rows.",
    },
    {
        "key": "ABC-102",
        "summary": "Add idempotency key to payment submission API",
        "status": "Done",
        "type": "Story",
        "description": "Callers retry on timeout; duplicate payments were observed in Q3.",
    },
    {
        "key": "ABC-103",
        "summary": "Ledger reconciliation drifts after daylight-saving change",
        "status": "Open",
        "type": "Bug",
        "description": "Cutover window is computed in local time instead of UTC.",
    },
]

_PAGES = {
    "ABC-ARCH": {
        "id": "ABC-ARCH",
        "title": "Application ABC - Architecture",
        "space": "PAY",
        "body": (
            "ABC is the payment submission gateway. It accepts requests from the channel "
            "layer, validates them against the business rule engine, and publishes to the "
            "settlement topic. Downstream: LEDGER, NOTIFY. Upstream: CHANNEL-API."
        ),
    },
    "ABC-RULES": {
        "id": "ABC-RULES",
        "title": "Application ABC - Business Rules",
        "space": "PAY",
        "body": (
            "R1: payments over 10,000 require dual approval. "
            "R2: cross-border payments must carry an FX quote id. "
            "R3: submissions are idempotent on (client_id, idempotency_key)."
        ),
    },
}


@mcp.tool()
def jira_search(jql: str, limit: int = 25) -> str:
    """Search Jira issues with a JQL query. Returns matching issues."""
    matched = [issue for issue in _ISSUES if _matches(issue, jql)] or _ISSUES
    return json.dumps({"total": len(matched[:limit]), "issues": matched[:limit]})


@mcp.tool()
def jira_get_issue(issue_key: str) -> str:
    """Fetch a single Jira issue by key, e.g. ABC-101."""
    for issue in _ISSUES:
        if issue["key"].lower() == issue_key.lower():
            return json.dumps(issue)
    return json.dumps({"error": f"issue {issue_key} not found"})


@mcp.tool()
def confluence_search(query: str, limit: int = 10) -> str:
    """Search Confluence pages by text. Returns page summaries."""
    hits = [
        {"id": page["id"], "title": page["title"], "space": page["space"]}
        for page in _PAGES.values()
        if query.lower() in (page["title"] + page["body"]).lower()
    ] or [
        {"id": page["id"], "title": page["title"], "space": page["space"]}
        for page in _PAGES.values()
    ]
    return json.dumps({"results": hits[:limit]})


@mcp.tool()
def confluence_get_page(page_id: str) -> str:
    """Fetch the full body of a Confluence page by id."""
    page = _PAGES.get(page_id)
    if page is None:
        return json.dumps({"error": f"page {page_id} not found"})
    return json.dumps(page)


@mcp.tool()
def fixture_create_page(title: str, body: str) -> str:
    """Create a page. Present only to prove the tool filter blocks writes."""
    return json.dumps({"created": title})


def _matches(issue: dict[str, str], jql: str) -> bool:
    needle = jql.lower()
    return any(token in needle for token in (issue["key"].lower(), issue["type"].lower()))


if __name__ == "__main__":
    mcp.run()
