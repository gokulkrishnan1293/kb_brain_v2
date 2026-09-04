"""The API is the platform contract; these tests exercise it as a consumer would."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from kb_brain.api.app import create_app
from kb_brain.platform import KnowledgeBrain
from tests.scripted_model import ScriptedChatModel


@pytest.fixture
def client(brain: KnowledgeBrain, monkeypatch) -> TestClient:
    monkeypatch.setattr(
        "kb_brain.agents.ingestion.agent.build_chat_model",
        lambda *a, **k: ScriptedChatModel(
            script=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "jira_search", "args": {"jql": "Bug"}, "id": "c1"}],
                ),
                AIMessage(content="Pulled the ABC bugs."),
            ]
        ),
    )
    with TestClient(create_app(brain)) as test_client:
        yield test_client


def test_health(client: TestClient):
    assert client.get("/health").json()["status"] == "ok"


def test_lists_sources_and_readiness(client: TestClient):
    body = client.get("/v1/sources").json()
    assert "jira" in body["sources"]
    servers = {entry["name"]: entry for entry in body["servers"]}
    assert servers["fixture"]["status"] == "connected"
    assert servers["needs_creds"]["status"] == "not_configured"


def test_lists_tools_for_a_source(client: TestClient):
    body = client.get("/v1/sources/jira/tools").json()
    assert "jira_search" in {tool["name"] for tool in body["tools"]}


def test_unconfigured_source_returns_424(client: TestClient):
    response = client.get("/v1/sources/github/tools")
    assert response.status_code == 424
    assert response.json()["error"] == "source_unavailable"


def test_ingestion_runs_synchronously_when_asked(client: TestClient):
    response = client.post(
        "/v1/ingestions?wait=true",
        headers={"X-KB-Principal": "admin"},
        json={
            "scope": "program:payments/application:abc",
            "sources": ["jira"],
            "objective": "Capture known defects for ABC",
        },
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["records_written"] == 2  # the two bugs the fixture JQL matches
    assert body["scope"] == "program:payments/application:abc"


def test_ingestion_returns_a_run_id_when_backgrounded(client: TestClient):
    response = client.post(
        "/v1/ingestions",
        headers={"X-KB-Principal": "admin"},
        json={
            "scope": "application:abc",
            "sources": ["jira"],
            "objective": "Capture known defects for ABC",
        },
    )
    assert response.status_code == 202
    run_id = response.json()["run_id"]

    status = client.get(f"/v1/ingestions/{run_id}").json()
    assert status["status"] in {"running", "succeeded"}


def test_ingestion_is_denied_for_an_unauthorized_principal(client: TestClient):
    response = client.post(
        "/v1/ingestions?wait=true",
        headers={"X-KB-Principal": "jira-bot"},
        json={
            "scope": "application:abc",
            "sources": ["confluence"],
            "objective": "anything",
        },
    )
    assert response.status_code == 403
    assert response.json()["error"] == "policy_denied"


def test_malformed_scope_is_rejected(client: TestClient):
    response = client.post(
        "/v1/ingestions?wait=true",
        headers={"X-KB-Principal": "admin"},
        json={"scope": "not-a-scope", "sources": ["jira"], "objective": "x"},
    )
    assert response.status_code == 400


def test_readback_requires_read_permission(client: TestClient):
    client.post(
        "/v1/ingestions?wait=true",
        headers={"X-KB-Principal": "admin"},
        json={
            "scope": "application:abc",
            "sources": ["jira"],
            "objective": "Capture known defects",
        },
    )
    allowed = client.get(
        "/v1/knowledge/raw", params={"scope": "application:abc"},
        headers={"X-KB-Principal": "admin"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["count"] == 2

    denied = client.get(
        "/v1/knowledge/raw", params={"scope": "application:abc"},
        headers={"X-KB-Principal": "roleless"},
    )
    assert denied.status_code == 403
