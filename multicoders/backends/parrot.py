"""In-process backend wrapping a ``parrot.bots.agent.BasicAgent``."""
from __future__ import annotations

from .base import AgentContext, Artifact, BackendUnavailable, TaskSpec


class ParrotBackend:
    """Generate artifacts with an in-process Parrot agent (uses provider keys)."""

    def __init__(self, name: str, role: str, *, use_tools: bool = False) -> None:
        self.name = name
        self.role = role
        self._use_tools = use_tools
        self._agent = None

    def capabilities(self) -> set[str]:
        return {"code", "review", "doc"}

    def _ensure_agent(self):
        if self._agent is None:
            from ..dispatcher import _load_basic_agent
            from ..prompts import get_coder_system_prompt

            BasicAgent = _load_basic_agent()
            self._agent = BasicAgent(
                name=self.name,
                agent_id=self.name.lower(),
                system_prompt=get_coder_system_prompt(self.name, self.role),
                use_tools=self._use_tools,
            )
        return self._agent

    async def health(self) -> bool:
        try:
            self._ensure_agent()
            return True
        except Exception:  # noqa: BLE001 - health probe must not raise
            return False

    async def generate(self, task: TaskSpec, ctx: AgentContext) -> Artifact:
        try:
            agent = self._ensure_agent()
        except RuntimeError as exc:
            raise BackendUnavailable(str(exc)) from exc
        response = await agent.ask(ctx.effective_prompt(task))
        return Artifact(
            backend=self.name,
            content=str(response.content).strip(),
            kind=task.kind,
        )
