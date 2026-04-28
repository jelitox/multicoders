"""Dispatcher-LITE: produces N candidate artifacts per task.

For the MVP this is a deterministic mock: each registered "coder" is a
callable that returns a code string. Later this gets swapped for real
LLM-backed coders wrapped as parrot FlowNodes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List

from .storage import Storage


CoderFn = Callable[[str], str]


@dataclass
class Candidate:
    artifact_id: int
    author: str
    content: str


class Dispatcher:
    def __init__(self, storage: Storage, coders: Dict[str, CoderFn]) -> None:
        if not coders:
            raise ValueError("Dispatcher needs at least one coder")
        self.storage = storage
        self.coders = coders

    def dispatch(self, task_id: str, prompt: str) -> List[Candidate]:
        self.storage.update_task_status(task_id, "in_progress")
        candidates: List[Candidate] = []
        for author, fn in self.coders.items():
            content = fn(prompt)
            artifact_id = self.storage.add_artifact(task_id, author, content)
            candidates.append(
                Candidate(artifact_id=artifact_id, author=author, content=content)
            )
        return candidates
