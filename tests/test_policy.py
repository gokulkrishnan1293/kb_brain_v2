from __future__ import annotations

import pytest

from kb_brain.errors import PolicyDenied
from kb_brain.governance import Action, PolicyEngine
from kb_brain.scope import ScopeRef

APP = ScopeRef.parse("program:payments/application:abc")
OTHER = ScopeRef.parse("program:lending/application:xyz")


def test_admin_may_do_anything(policy: PolicyEngine):
    assert policy.authorize("admin", Action.PUBLISH, APP).allowed


def test_unknown_principal_is_denied(policy: PolicyEngine):
    decision = policy.authorize("nobody@example.com", Action.READ, APP)
    assert not decision.allowed
    assert "no roles" in decision.reason


def test_roleless_principal_is_denied(policy: PolicyEngine):
    assert not policy.authorize("roleless", Action.INGEST, APP).allowed


def test_source_restriction_is_enforced(policy: PolicyEngine):
    assert policy.authorize("jira-bot", Action.INGEST, APP, source="jira").allowed
    denied = policy.authorize("jira-bot", Action.INGEST, APP, source="confluence")
    assert not denied.allowed


def test_scope_grant_covers_descendants_but_not_siblings(policy: PolicyEngine):
    assert policy.authorize("pay-owner", Action.CURATE, APP).allowed
    assert not policy.authorize("pay-owner", Action.CURATE, OTHER).allowed


def test_explicit_deny_beats_allow(policy: PolicyEngine):
    assert policy.authorize("curator", Action.CURATE, APP).allowed
    assert not policy.authorize("curator", Action.PUBLISH, APP).allowed


def test_raise_for_denial(policy: PolicyEngine):
    with pytest.raises(PolicyDenied):
        policy.authorize("roleless", Action.READ, APP).raise_for_denial()
