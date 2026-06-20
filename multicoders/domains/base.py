"""DomainProfile: make the orchestrator domain-pluggable (SPEC §3.3).

The pipeline (Research -> Dispatch -> Arena -> Validate -> Complete) stays
domain-agnostic; only the :class:`DomainProfile` swaps. A profile defines the
artifact kind, the cheap objective filter run before judging, and the
validators that gate the winner.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class ValidationOutcome:
    """Result of a profile's validation gate over a winning artifact."""

    passed: bool
    reason: str
    needs_human: bool = False


@runtime_checkable
class DomainProfile(Protocol):
    """Domain-specific filter + validators for an orchestration run."""

    name: str
    artifact_kind: str

    def objective_filter(self, content: str) -> tuple[bool, str]:
        """Cheap pre-judging gate. Returns ``(ok, reason)``."""
        ...

    def validate(self, content: str, *, workdir: str | None = None) -> ValidationOutcome:
        """Validate a winning artifact against the domain's success criteria."""
        ...
