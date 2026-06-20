"""AgentBackend protocol and shared types (SPEC §3.1).

A single abstraction so both engines (the deterministic Parrot *arena* and the
CLI *council*) consume agents uniformly. Concrete backends live in sibling
modules:

- :class:`~multicoders.backends.mock.MockBackend` — deterministic, dry-run/tests.
- :class:`~multicoders.backends.parrot.ParrotBackend` — in-process ``parrot.bots``.
- :class:`~multicoders.backends.cli.CliBackend` — official provider CLI, BYO-auth.
- :class:`~multicoders.backends.api.ApiBackend` — provider API keys (pay-per-token).
- :class:`~multicoders.backends.api.LocalBackend` — local models (Ollama).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class TaskSpec:
    """A unit of work handed to a backend, independent of the domain."""

    prompt: str
    task_id: str = ""
    kind: str = "code"  # domain hint: "code", "review", "doc", "process"...
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentContext:
    """Per-call context: role, grounding, and a memory handle (Fase 4)."""

    role: str = ""
    system_prompt: str | None = None
    enriched_prompt: str | None = None
    memory: Any = None
    extra: dict[str, Any] = field(default_factory=dict)

    def effective_prompt(self, task: "TaskSpec") -> str:
        """Prefer enriched/grounded context over the raw task prompt."""
        return self.enriched_prompt or task.prompt


@dataclass
class Artifact:
    """A backend's output for a task."""

    backend: str
    content: str
    kind: str = "code"
    metadata: dict[str, Any] = field(default_factory=dict)


class BackendError(RuntimeError):
    """A backend failed to produce an artifact."""


class BackendUnavailable(BackendError):
    """A backend cannot run (missing binary, deps, auth, or credentials)."""


@runtime_checkable
class AgentBackend(Protocol):
    """The uniform agent interface both engines consume."""

    name: str

    def capabilities(self) -> set[str]:
        """Domains this backend can serve, e.g. ``{"code", "review", "doc"}``."""
        ...

    async def health(self) -> bool:
        """Cheap readiness probe (binary present, deps importable, ...)."""
        ...

    async def generate(self, task: TaskSpec, ctx: AgentContext) -> Artifact:
        """Produce an artifact for ``task`` using ``ctx`` for grounding."""
        ...
