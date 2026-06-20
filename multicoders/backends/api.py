"""API-key and local-model backends.

- :class:`ApiBackend` uses provider API keys (pay-per-token). It is for CI,
  headless runs, and contributors without a CLI subscription. It is NEVER
  selected automatically — the core must not silently fall back to API billing;
  it must be configured explicitly.
- :class:`LocalBackend` targets a local model server (Ollama) for zero-cost
  contributors.
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request

from .base import AgentContext, Artifact, BackendError, BackendUnavailable, TaskSpec

_API_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
}


class ApiBackend:
    """Provider API backend (pay-per-token) via in-process Parrot clients.

    Wraps a Parrot ``BasicAgent`` like :class:`ParrotBackend`, but is named and
    documented as an explicit, billed path so callers opt in knowingly.
    """

    def __init__(self, name: str, role: str, *, provider: str = "anthropic") -> None:
        self.name = name
        self.role = role
        self.provider = provider
        self._agent = None

    def capabilities(self) -> set[str]:
        return {"code", "review", "doc"}

    async def health(self) -> bool:
        env = _API_KEY_ENV.get(self.provider)
        return bool(env and os.environ.get(env))

    def _ensure_agent(self):
        if self._agent is None:
            from ..dispatcher import _load_basic_agent
            from ..prompts import get_coder_system_prompt

            BasicAgent = _load_basic_agent()
            self._agent = BasicAgent(
                name=self.name,
                agent_id=self.name.lower(),
                system_prompt=get_coder_system_prompt(self.name, self.role),
                use_tools=False,
            )
        return self._agent

    async def generate(self, task: TaskSpec, ctx: AgentContext) -> Artifact:
        if not await self.health():
            raise BackendUnavailable(
                f"ApiBackend requires {_API_KEY_ENV.get(self.provider)} to be set"
            )
        agent = self._ensure_agent()
        response = await agent.ask(ctx.effective_prompt(task))
        return Artifact(
            backend=self.name,
            content=str(response.content).strip(),
            kind=task.kind,
            metadata={"provider": self.provider, "auth": "api-key"},
        )


class LocalBackend:
    """Local-model backend talking to an Ollama-compatible HTTP endpoint."""

    def __init__(
        self,
        name: str = "ollama",
        *,
        model: str = "llama3",
        host: str | None = None,
        timeout_sec: int = 600,
    ) -> None:
        self.name = name
        self.model = model
        self.host = (host or os.environ.get("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
        self.timeout_sec = timeout_sec

    def capabilities(self) -> set[str]:
        return {"code", "review", "doc", "process"}

    async def health(self) -> bool:
        def _probe() -> bool:
            try:
                with urllib.request.urlopen(f"{self.host}/api/tags", timeout=3) as resp:
                    return resp.status == 200
            except (urllib.error.URLError, OSError):
                return False

        return await asyncio.to_thread(_probe)

    async def generate(self, task: TaskSpec, ctx: AgentContext) -> Artifact:
        payload = json.dumps(
            {"model": self.model, "prompt": ctx.effective_prompt(task), "stream": False}
        ).encode("utf-8")

        def _call() -> str:
            req = urllib.request.Request(
                f"{self.host}/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
            except (urllib.error.URLError, OSError) as exc:
                raise BackendUnavailable(f"Ollama not reachable at {self.host}: {exc}") from exc
            except json.JSONDecodeError as exc:
                raise BackendError(f"Ollama returned invalid JSON: {exc}") from exc
            return str(body.get("response", "")).strip()

        content = await asyncio.to_thread(_call)
        return Artifact(
            backend=self.name,
            content=content,
            kind=task.kind,
            metadata={"provider": "ollama", "model": self.model, "auth": "local"},
        )
