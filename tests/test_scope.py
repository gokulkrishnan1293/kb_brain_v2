from __future__ import annotations

import pytest

from kb_brain.errors import ScopeError
from kb_brain.scope import ScopeLevel, ScopeRef


def test_parses_single_segment():
    scope = ScopeRef.parse("application:ABC")
    assert scope.level is ScopeLevel.APPLICATION
    assert scope.leaf.key == "abc"  # keys normalise to lowercase
    assert scope.render() == "application:abc"


def test_parses_nested_path_and_exposes_ancestors():
    scope = ScopeRef.parse("domain:finance/program:payments/application:abc")
    assert [ancestor.render() for ancestor in scope.ancestors()] == [
        "domain:finance",
        "domain:finance/program:payments",
    ]
    assert scope.to_path().as_posix() == "domain/finance/program/payments/application/abc"


def test_rejects_out_of_order_levels():
    with pytest.raises(ScopeError, match="coarse to fine"):
        ScopeRef.parse("application:abc/program:payments")


def test_rejects_repeated_level():
    with pytest.raises(ScopeError):
        ScopeRef.parse("application:abc/application:xyz")


@pytest.mark.parametrize("raw", ["", "abc", "widget:abc", "application:", "application:a b"])
def test_rejects_malformed_input(raw: str):
    with pytest.raises(ScopeError):
        ScopeRef.parse(raw)
