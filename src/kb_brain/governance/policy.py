"""Policy engine.

IAM-shaped and deliberately small: identity -> role -> statement -> decision.
Every knowledge operation passes through :meth:`PolicyEngine.authorize` before
anything touches a source system or storage.

Evaluation rules:
  * explicit ``deny`` always wins
  * absence of an ``allow`` is a denial (default-deny)
  * ``*`` is a glob, matched with fnmatch, on action, scope and source
"""

from __future__ import annotations

from enum import StrEnum
from fnmatch import fnmatch
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from ..errors import ConfigError, PolicyDenied
from ..scope import ScopeRef


class Action(StrEnum):
    """The governed operations. Mirrors the platform's lifecycle layers."""

    INGEST = "knowledge:ingest"
    CURATE = "knowledge:curate"
    PUBLISH = "knowledge:publish"
    READ = "knowledge:read"
    ADMIN = "platform:admin"


class Statement(BaseModel):
    effect: Literal["allow", "deny"] = "allow"
    actions: list[str] = Field(default_factory=lambda: ["*"])
    scopes: list[str] = Field(default_factory=lambda: ["*"])
    sources: list[str] = Field(default_factory=lambda: ["*"])

    def matches(self, action: Action, scope: ScopeRef, source: str | None) -> bool:
        if not any(fnmatch(action.value, pattern) for pattern in self.actions):
            return False
        if not self._scope_matches(scope):
            return False
        if source is not None and not any(fnmatch(source, pattern) for pattern in self.sources):
            return False
        return True

    def _scope_matches(self, scope: ScopeRef) -> bool:
        """A statement matches if its pattern matches the scope or any ancestor.

        Granting ``program:payments/*`` therefore covers every application and
        component beneath that program without enumerating them.
        """
        candidates = [scope.render(), *(ancestor.render() for ancestor in scope.ancestors())]
        return any(
            fnmatch(candidate, pattern) for candidate in candidates for pattern in self.scopes
        )


class Role(BaseModel):
    description: str = ""
    statements: list[Statement] = Field(default_factory=list)


class Principal(BaseModel):
    """A human or an agent. Both are first-class; both are governed."""

    id: str
    kind: Literal["user", "agent", "service"] = "user"
    display_name: str | None = None
    roles: list[str] = Field(default_factory=list)

    @classmethod
    def anonymous(cls, principal_id: str = "anonymous") -> Principal:
        return cls(id=principal_id, kind="user", roles=[])


class PolicyDecision(BaseModel):
    """The recorded outcome of an authorization check.

    Carried into the run state so the provenance of a knowledge object
    includes the authority under which it was created.
    """

    allowed: bool
    principal: str
    action: Action
    scope: str
    source: str | None = None
    reason: str
    matched_role: str | None = None

    def raise_for_denial(self) -> None:
        if not self.allowed:
            raise PolicyDenied(
                f"{self.principal} is not permitted to {self.action.value} "
                f"on {self.scope}: {self.reason}",
                reason=self.reason,
            )


class PolicyDocument(BaseModel):
    roles: dict[str, Role] = Field(default_factory=dict)
    principals: list[Principal] = Field(default_factory=list)


class PolicyEngine:
    """Evaluates policy documents. Swap the loader to move to a real IAM store."""

    def __init__(self, document: PolicyDocument) -> None:
        self._doc = document
        self._principals = {principal.id: principal for principal in document.principals}

    @classmethod
    def from_file(cls, path: Path) -> PolicyEngine:
        if not path.exists():
            raise ConfigError(f"policy file not found: {path}")
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
        try:
            document = PolicyDocument.model_validate(raw)
        except ValueError as exc:
            raise ConfigError(f"invalid policy document {path}: {exc}") from exc
        return cls(document)

    def resolve_principal(self, principal_id: str) -> Principal:
        """Unknown identities resolve to a role-less principal, not an error.

        Default-deny then does the work: an unknown caller can do nothing.
        """
        return self._principals.get(principal_id) or Principal.anonymous(principal_id)

    def authorize(
        self,
        principal: Principal | str,
        action: Action,
        scope: ScopeRef,
        *,
        source: str | None = None,
    ) -> PolicyDecision:
        if isinstance(principal, str):
            principal = self.resolve_principal(principal)

        base = {
            "principal": principal.id,
            "action": action,
            "scope": scope.render(),
            "source": source,
        }

        if not principal.roles:
            return PolicyDecision(
                allowed=False, reason="principal has no roles assigned", **base
            )

        allow_match: str | None = None
        for role_name in principal.roles:
            role = self._doc.roles.get(role_name)
            if role is None:
                continue
            for statement in role.statements:
                if not statement.matches(action, scope, source):
                    continue
                if statement.effect == "deny":
                    # Explicit deny short-circuits every allow.
                    return PolicyDecision(
                        allowed=False,
                        reason=f"explicit deny in role '{role_name}'",
                        matched_role=role_name,
                        **base,
                    )
                allow_match = allow_match or role_name

        if allow_match:
            return PolicyDecision(
                allowed=True,
                reason=f"allowed by role '{allow_match}'",
                matched_role=allow_match,
                **base,
            )
        return PolicyDecision(
            allowed=False,
            reason="no matching allow statement (default deny)",
            **base,
        )
