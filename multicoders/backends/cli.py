"""Backend that drives an *official* provider CLI in the user's own session.

HARD CONSTRAINT (SPEC §3.1, non-negotiable)
-------------------------------------------
This backend MUST use each provider's official login flow, one account per user,
bring-your-own-auth. It MUST NEVER proxy, pool, or redistribute subscription
tokens; never call reverse-engineered/undocumented endpoints; never share
credentials. It also MUST NOT inject a provider API key into the CLI's
environment — doing so silently switches the session to API (pay-per-token)
billing. The core must never silently fall back to API billing.

The actual subprocess invocation is delegated to :mod:`multicoders.providers`
(``run_provider``), which spawns the official binary (``claude`` / ``codex`` /
``gemini``) in the repo directory and captures stdout as the artifact.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from .base import AgentContext, Artifact, BackendError, BackendUnavailable, TaskSpec

class CliBackend:
    """Run the official provider CLI (BYO-auth) and capture its output."""

    def __init__(
        self,
        provider: str,
        *,
        repo: str | Path = ".",
        model: str | None = None,
        timeout_sec: int = 600,
        name: str | None = None,
        strip_api_keys: bool = True,
    ) -> None:
        self.provider = provider
        self.name = name or provider
        self.repo = Path(repo)
        self.model = model
        self.timeout_sec = timeout_sec
        self.strip_api_keys = strip_api_keys

    def capabilities(self) -> set[str]:
        return {"code", "review", "doc", "process"}

    async def health(self) -> bool:
        from ..providers import provider_available

        return provider_available(self.provider)

    async def generate(self, task: TaskSpec, ctx: AgentContext) -> Artifact:
        from ..providers import ProviderError, run_provider

        if not await self.health():
            raise BackendUnavailable(
                f"provider CLI not available: {self.provider} "
                f"(install it and run its official login flow)"
            )
        prompt = ctx.effective_prompt(task)
        try:
            result = await asyncio.to_thread(
                run_provider,
                self.provider,
                prompt,
                self.repo,
                self.model,
                self.timeout_sec,
                allow_api_keys=not self.strip_api_keys,
            )
        except ProviderError as exc:
            raise BackendError(f"{self.provider} CLI failed: {exc}") from exc
        return Artifact(
            backend=self.name,
            content=result.text_output(),
            kind=task.kind,
            metadata={"provider": self.provider, "auth": "byo-cli"},
        )
