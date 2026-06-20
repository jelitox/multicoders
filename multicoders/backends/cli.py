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
import os
from pathlib import Path

from .base import AgentContext, Artifact, BackendError, BackendUnavailable, TaskSpec

# Provider API-key env vars that, if present, switch the official CLI to
# pay-per-token API billing. CliBackend strips them from the child env so a
# subscription session is never silently billed as API usage.
_API_KEY_ENV_VARS: dict[str, tuple[str, ...]] = {
    "claude": ("ANTHROPIC_API_KEY",),
    "codex": ("OPENAI_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI"),
}


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

    def _guard_byo_auth(self) -> None:
        """Enforce the no-silent-API-billing rule before invoking the CLI."""
        if not self.strip_api_keys:
            return
        # Strip provider API keys from THIS process env so run_provider (which
        # copies os.environ for the child) cannot leak them into the CLI.
        for var in _API_KEY_ENV_VARS.get(self.provider, ()):  # noqa: B007
            os.environ.pop(var, None)

    async def generate(self, task: TaskSpec, ctx: AgentContext) -> Artifact:
        from ..providers import ProviderError, run_provider

        if not await self.health():
            raise BackendUnavailable(
                f"provider CLI not available: {self.provider} "
                f"(install it and run its official login flow)"
            )
        self._guard_byo_auth()
        prompt = ctx.effective_prompt(task)
        try:
            result = await asyncio.to_thread(
                run_provider,
                self.provider,
                prompt,
                self.repo,
                self.model,
                self.timeout_sec,
            )
        except ProviderError as exc:
            raise BackendError(f"{self.provider} CLI failed: {exc}") from exc
        return Artifact(
            backend=self.name,
            content=result.text_output(),
            kind=task.kind,
            metadata={"provider": self.provider, "auth": "byo-cli"},
        )
