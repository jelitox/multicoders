"""Dispatcher: produces N candidate artifacts per task.

The production path uses Parrot ``BasicAgent`` instances, but the module
must stay importable without Parrot installed so ``--dry-run`` and unit
tests can validate the DAG without LLM credentials or heavy dependencies.
"""
from __future__ import annotations

import asyncio
import inspect
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Mapping, Optional, Protocol, Union

from .prompts import get_coder_system_prompt
from .storage import Storage


_SAFE_AUTHOR = re.compile(r"[^a-zA-Z0-9_.-]+")


@dataclass
class Candidate:
    artifact_id: int
    author: str
    content: str
    workdir: Optional[str] = None


def _strip_code_fences(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1 :]
        if text.endswith("```"):
            text = text[: -3]
    return text.rstrip() + "\n"


def write_candidate_worktree(
    root: Path, task_id: str, author: str, content: str
) -> Path:
    safe_author = _SAFE_AUTHOR.sub("_", author) or "anon"
    target_dir = Path(root) / task_id / safe_author
    target_dir.mkdir(parents=True, exist_ok=True)
    candidate_file = target_dir / "candidate.py"
    candidate_file.write_text(_strip_code_fences(content), encoding="utf-8")
    return target_dir


class Coder(Protocol):
    name: str

    async def generate(self, prompt: str) -> str:
        ...


def _load_basic_agent():
    try:
        from parrot.bots.agent import BasicAgent

        return BasicAgent
    except ModuleNotFoundError:
        parrot_src = (
            Path(__file__).resolve().parents[1]
            / "_refs"
            / "ai-parrot"
            / "packages"
            / "ai-parrot"
            / "src"
        )
        if parrot_src.exists() and str(parrot_src) not in sys.path:
            sys.path.insert(0, str(parrot_src))
        try:
            from parrot.bots.agent import BasicAgent

            return BasicAgent
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Parrot is not importable. Install this project with its local "
                "ai-parrot dependency or run the CLI with --dry-run."
            ) from exc


def _run_async(awaitable):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    raise RuntimeError("Dispatcher.dispatch cannot run inside an active event loop")


class CallableCoder:
    def __init__(self, name: str, fn: Callable[[str], str]) -> None:
        self.name = name
        self._fn = fn

    async def generate(self, prompt: str) -> str:
        result = self._fn(prompt)
        if inspect.isawaitable(result):
            result = await result
        return str(result).strip()


class ParrotCoder:
    def __init__(self, name: str, role: str):
        BasicAgent = _load_basic_agent()
        self.name = name
        self.agent = BasicAgent(
            name=name,
            agent_id=name.lower(),
            system_prompt=get_coder_system_prompt(name, role),
            use_tools=False,
        )

    async def generate(self, prompt: str) -> str:
        response = await self.agent.ask(prompt)
        return str(response.content).strip()


class Dispatcher:
    def __init__(
        self,
        storage: Storage,
        coders: Union[Mapping[str, Callable[[str], str]], Iterable[Coder]],
        worktree_root: Optional[Union[str, Path]] = None,
    ) -> None:
        coders = self._normalize_coders(coders)
        if not coders:
            raise ValueError("Dispatcher needs at least one coder")
        self.storage = storage
        self.coders: List[Coder] = coders
        self.worktree_root = Path(worktree_root) if worktree_root else None

    @staticmethod
    def _normalize_coders(
        coders: Union[Mapping[str, Callable[[str], str]], Iterable[Coder]],
    ) -> List[Coder]:
        if isinstance(coders, Mapping):
            return [CallableCoder(name, fn) for name, fn in coders.items()]
        return list(coders)

    def dispatch(self, task_id: str, prompt: str) -> List[Candidate]:
        return _run_async(self.dispatch_async(task_id, prompt))

    async def dispatch_async(self, task_id: str, prompt: str) -> List[Candidate]:
        self.storage.update_task_status(task_id, "in_progress")

        # Run all coders in parallel for maximum efficiency
        async def _generate_candidate(coder: Coder) -> Candidate:
            content = await coder.generate(prompt)
            artifact_id = self.storage.add_artifact(task_id, coder.name, content)
            workdir: Optional[str] = None
            if self.worktree_root is not None:
                target = write_candidate_worktree(
                    self.worktree_root, task_id, coder.name, content
                )
                workdir = str(target)
                self.storage.update_artifact_workdir(artifact_id, workdir)
            return Candidate(
                artifact_id=artifact_id,
                author=coder.name,
                content=content,
                workdir=workdir,
            )

        tasks = [_generate_candidate(coder) for coder in self.coders]
        candidates = await asyncio.gather(*tasks)
        return list(candidates)
