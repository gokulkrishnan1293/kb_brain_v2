"""Knowledge scopes (the six-level model).

A scope is not a label. It is the addressing scheme for the whole platform:
it selects the harness that processes knowledge, the policies that gate the
operation, and the location the result is published to.

Wire format is a path of ``level:key`` segments, coarsest first::

    application:abc
    program:payments/application:abc
    domain:finance/program:payments/application:abc/component:ledger-api
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator

from .errors import ScopeError

_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class ScopeLevel(StrEnum):
    """L1..L6. Order of declaration is the hierarchy order."""

    ENTERPRISE = "enterprise"
    DOMAIN = "domain"
    PROGRAM = "program"
    APPLICATION = "application"
    COMPONENT = "component"
    KNOWLEDGE = "knowledge"

    @property
    def depth(self) -> int:
        return _LEVEL_ORDER[self]


_LEVEL_ORDER: dict[ScopeLevel, int] = {level: i + 1 for i, level in enumerate(ScopeLevel)}


class ScopeSegment(BaseModel):
    level: ScopeLevel
    key: str

    @field_validator("key")
    @classmethod
    def _check_key(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _KEY_RE.match(normalized):
            raise ValueError(
                f"invalid scope key {value!r}: use lowercase alphanumerics, '.', '_' or '-'"
            )
        return normalized

    def render(self) -> str:
        return f"{self.level.value}:{self.key}"


class ScopeRef(BaseModel):
    """A fully qualified reference to a place in the knowledge hierarchy."""

    segments: list[ScopeSegment] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_hierarchy(self) -> ScopeRef:
        seen: list[int] = []
        for segment in self.segments:
            depth = segment.level.depth
            if seen and depth <= seen[-1]:
                raise ValueError(
                    "scope segments must go from coarse to fine without repeating a level; "
                    f"got {self.render()}"
                )
            seen.append(depth)
        return self

    # -- construction ---------------------------------------------------------

    @classmethod
    def parse(cls, raw: str) -> ScopeRef:
        """Parse ``program:payments/application:abc`` into a ScopeRef."""
        text = (raw or "").strip().strip("/")
        if not text:
            raise ScopeError("scope must not be empty")
        segments: list[ScopeSegment] = []
        for part in text.split("/"):
            if ":" not in part:
                raise ScopeError(
                    f"invalid scope segment {part!r}: expected '<level>:<key>', "
                    f"e.g. 'application:abc'"
                )
            level_raw, _, key = part.partition(":")
            try:
                level = ScopeLevel(level_raw.strip().lower())
            except ValueError as exc:
                valid = ", ".join(level.value for level in ScopeLevel)
                raise ScopeError(
                    f"unknown scope level {level_raw!r}; valid levels: {valid}"
                ) from exc
            try:
                segments.append(ScopeSegment(level=level, key=key))
            except ValueError as exc:
                raise ScopeError(str(exc)) from exc
        try:
            return cls(segments=segments)
        except ValueError as exc:
            raise ScopeError(str(exc)) from exc

    # -- accessors ------------------------------------------------------------

    @property
    def leaf(self) -> ScopeSegment:
        """The most specific segment. This selects the harness."""
        return self.segments[-1]

    @property
    def level(self) -> ScopeLevel:
        return self.leaf.level

    def render(self) -> str:
        return "/".join(segment.render() for segment in self.segments)

    def to_path(self) -> Path:
        """Filesystem-safe layout: ``program/payments/application/abc``."""
        parts: list[str] = []
        for segment in self.segments:
            parts.extend([segment.level.value, segment.key])
        return Path(*parts)

    def ancestors(self) -> list[ScopeRef]:
        """Every enclosing scope, coarsest first, excluding self."""
        return [
            ScopeRef(segments=self.segments[: i + 1]) for i in range(len(self.segments) - 1)
        ]

    def __str__(self) -> str:  # pragma: no cover - convenience
        return self.render()
