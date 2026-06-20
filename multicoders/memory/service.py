"""MemoryService — the layered memory facade (SPEC §3.2).

Wires the memory layers behind one object both engines consume. The
transactional ledger stays in SQLite (``Storage``); this facade carries
*context and learning*:

- working   — per-run phase staging (Fase 4a, available now)
- decisions — cross-run episodic recall (Fase 4a baseline; GraphIndex in 4b)
- documents — repo/code grounding (PageIndex, Fase 4b)
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

from .base import (
    Decision,
    DecisionMemoryLayer,
    DocumentMemoryLayer,
    Recall,
    WorkingMemoryLayer,
)
from .decision import JsonDecisionMemory
from .working import InProcessWorkingMemory


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


class MemoryService:
    """Facade over the memory layers, with graceful degradation per layer."""

    def __init__(
        self,
        *,
        working: WorkingMemoryLayer | None = None,
        decisions: DecisionMemoryLayer | None = None,
        documents: DocumentMemoryLayer | None = None,
    ) -> None:
        self.working = working or InProcessWorkingMemory()
        self.decisions = decisions
        self.documents = documents

    # ---- factory ---------------------------------------------------------
    @classmethod
    def local(cls, state_dir: str | Path = ".multicoders") -> "MemoryService":
        """A dependency-free MemoryService backed by local files (Fase 4a)."""
        base = Path(state_dir)
        return cls(
            working=InProcessWorkingMemory(),
            decisions=JsonDecisionMemory(base / "decisions.jsonl"),
        )

    # ---- working memory --------------------------------------------------
    def stage(self, run_id: str, key: str, value: Any) -> None:
        self.working.stage(run_id, key, value)

    def staged(self, run_id: str, key: str | None = None) -> Any:
        return self.working.staged(run_id, key)

    def clear_run(self, run_id: str) -> None:
        self.working.clear_run(run_id)

    # ---- decision memory -------------------------------------------------
    def record_decision(
        self,
        *,
        task_id: str,
        prompt: str,
        winner: str,
        status: str,
        tags: list[str] | None = None,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self.decisions is None:
            return
        self.decisions.record(
            Decision(
                task_id=task_id,
                prompt=prompt,
                winner=winner,
                status=status,
                tags=tags or [],
                reason=reason,
                created_at=_utc_now_iso(),
                metadata=metadata or {},
            )
        )

    def recall_decisions(self, query: str, *, limit: int = 5) -> list[Recall]:
        if self.decisions is None:
            return []
        return self.decisions.recall(query, limit=limit)

    # ---- documental grounding (Fase 4b) ----------------------------------
    def ground(self, query: str, *, top_k: int = 5) -> str:
        if self.documents is None or not self.documents.available():
            return ""
        return self.documents.ground(query, top_k=top_k)

    # ---- introspection ---------------------------------------------------
    def layers_status(self) -> dict[str, bool]:
        return {
            "working": self.working is not None,
            "decisions": self.decisions is not None,
            "documents": bool(self.documents and self.documents.available()),
        }
