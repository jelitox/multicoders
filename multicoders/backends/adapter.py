"""Adapters bridging :class:`AgentBackend` to the legacy engine interfaces.

The arena ``Dispatcher`` consumes objects with ``name`` + ``async generate(prompt)
-> str`` (the ``Coder`` protocol), and ``Arena`` consumes judges with
``async judge(content) -> (vote, feedback)``. These adapters let a single
``AgentBackend`` plug into both without changing the existing engines.
"""
from __future__ import annotations

from .base import AgentBackend, AgentContext, TaskSpec


class BackendCoder:
    """Expose an :class:`AgentBackend` through the legacy ``Coder`` interface."""

    def __init__(
        self,
        backend: AgentBackend,
        *,
        role: str = "",
        system_prompt: str | None = None,
        kind: str = "code",
    ) -> None:
        self.backend = backend
        self.name = backend.name
        self._role = role
        self._system_prompt = system_prompt
        self._kind = kind

    async def generate(self, prompt: str) -> str:
        task = TaskSpec(prompt=prompt, kind=self._kind)
        ctx = AgentContext(role=self._role, system_prompt=self._system_prompt)
        artifact = await self.backend.generate(task, ctx)
        return artifact.content


class BackendJudge:
    """Expose an :class:`AgentBackend` through the legacy judge interface.

    The backend is asked to evaluate the artifact and must answer with a verdict
    that starts with ``approved``/``rejected``; the vote is parsed from the text.
    """

    def __init__(self, backend: AgentBackend, *, focus: str = "overall code quality") -> None:
        self.backend = backend
        self.name = backend.name
        self._focus = focus

    async def judge(self, artifact_content: str) -> tuple[str, str]:
        task = TaskSpec(
            prompt=(
                f"You are an expert judge focusing on {self._focus}. Evaluate the "
                "following and answer with 'approved' or 'rejected' followed by a "
                f"short technical reason.\n\n{artifact_content}"
            ),
            kind="review",
        )
        try:
            artifact = await self.backend.generate(task, AgentContext(role="judge"))
        except Exception as exc:  # noqa: BLE001 - a judge must never crash the arena
            return "rejected", f"Judge failure: {exc}"
        text = artifact.content.strip().lower()
        status = "approved" if "approved" in text or "approve" in text else "rejected"
        return status, artifact.content.strip()
