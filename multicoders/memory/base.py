"""Shared types and layer protocols for the MemoryService (SPEC §3.2).

The memory stack is layered. Fase 4a ships the two layers that need no
``ai-parrot`` bump (working + decision); the documental/episodic/procedural
layers are declared here as protocols and filled in Fase 4b once PageIndex /
GraphIndex / Skills are available.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class Decision:
    """A durable record of a completed orchestration outcome."""

    task_id: str
    prompt: str
    winner: str
    status: str
    tags: list[str] = field(default_factory=list)
    reason: str = ""
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Recall:
    """A scored hit from a memory query."""

    score: float
    decision: Decision


@runtime_checkable
class WorkingMemoryLayer(Protocol):
    """Short-term, per-run staging (replaces giant prompt concatenation)."""

    def stage(self, run_id: str, key: str, value: Any) -> None: ...
    def staged(self, run_id: str, key: str | None = None) -> Any: ...
    def clear_run(self, run_id: str) -> None: ...


@runtime_checkable
class DecisionMemoryLayer(Protocol):
    """Cross-run episodic memory: 'did we solve something like this before?'."""

    def record(self, decision: Decision) -> None: ...
    def recall(self, query: str, *, limit: int = 5) -> list[Recall]: ...


@runtime_checkable
class DocumentMemoryLayer(Protocol):
    """Documental/code grounding (PageIndex in Fase 4b)."""

    def available(self) -> bool: ...
    def ground(self, query: str, *, top_k: int = 5) -> str: ...
