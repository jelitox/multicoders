"""Deterministic backend for dry-run mode and tests (no LLM calls)."""
from __future__ import annotations

from .base import AgentContext, Artifact, TaskSpec


class MockBackend:
    """A backend that returns a fixed (or templated) artifact deterministically."""

    def __init__(
        self,
        name: str,
        content: str | None = None,
        *,
        capabilities: set[str] | None = None,
        healthy: bool = True,
    ) -> None:
        self.name = name
        self._content = content
        self._caps = set(capabilities or {"code", "review"})
        self._healthy = healthy

    def capabilities(self) -> set[str]:
        return set(self._caps)

    async def health(self) -> bool:
        return self._healthy

    async def generate(self, task: TaskSpec, ctx: AgentContext) -> Artifact:
        if self._content is not None:
            content = self._content
        elif task.kind == "review":
            content = f"approved: {self.name} mock judge ok"
        else:
            snippet = (task.prompt or "").strip().replace("'", "")[:40]
            content = f"def solution():\n    return '{self.name} mock: {snippet}'\n"
        return Artifact(backend=self.name, content=content, kind=task.kind)


def default_mock_backends() -> list[MockBackend]:
    """Two deterministic coder backends mirroring the old default mock coders."""
    return [
        MockBackend("Claude", "def hello():\n    return 'hello from claude mock'\n"),
        MockBackend("Gemini", "def hello():\n    return 'hello from gemini mock'\n"),
    ]
