"""Top-level deterministic orchestration: Research -> Dispatch -> Arena -> QA."""
from __future__ import annotations

import asyncio
import inspect
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .arena import Arena, ArenaVerdict
from .dispatcher import Candidate, Dispatcher
from .memory import MemoryService
from .qa import QANode, QAReport
from .research import ResearchNode
from .storage import Storage


@dataclass
class RoundResult:
    task_id: str
    candidates: List[Candidate]
    verdicts: List[ArenaVerdict]
    final_status: str
    winner: Optional[Candidate]
    qa_report: Optional[QAReport] = None


class MulticodersFlow:
    def __init__(
        self,
        storage: Storage,
        dispatcher: Dispatcher,
        arena: Arena,
        research_node: Optional[ResearchNode] = None,
        qa_node: Optional[QANode] = None,
        max_retries: int = 2,
        memory: Optional[MemoryService] = None,
    ) -> None:
        self.storage = storage
        self.dispatcher = dispatcher
        self.arena = arena
        self.research_node = research_node or ResearchNode(storage)
        self.qa_node = qa_node or QANode(storage)
        self.max_retries = max_retries
        self.memory = memory

    def run(self, prompt: str, payload: Optional[Dict[str, Any]] = None) -> RoundResult:
        task_id = str(uuid.uuid4())
        context = self.research_node.enrich(task_id, prompt)

        if self.memory is not None:
            self.memory.stage(task_id, "research", context.metadata)
            prior = self.memory.recall_decisions(prompt, limit=3)
            if prior:
                self.memory.stage(task_id, "prior_decisions", [r.decision.task_id for r in prior])

        initial_payload = dict(payload or {})
        initial_payload.update(
            {
                "prompt": prompt,
                "enriched_prompt": context.enriched_prompt,
                "metadata": context.metadata,
            }
        )
        self.storage.create_task(task_id, initial_payload)
        self.storage.save_checkpoint(
            task_id,
            "research",
            {"enriched": context.enriched_prompt, "metadata": context.metadata},
        )

        last_candidates: List[Candidate] = []
        last_verdicts: List[ArenaVerdict] = []

        for attempt in range(self.max_retries + 1):
            last_candidates = self._await(
                self.dispatcher.dispatch(task_id, context.enriched_prompt)
            )
            self.storage.save_checkpoint(
                task_id, "dispatcher", {"count": len(last_candidates)}, attempt
            )

            last_verdicts = self._await(self.arena.run(task_id, last_candidates))
            self.storage.save_checkpoint(
                task_id, "arena", {"verdicts": len(last_verdicts)}, attempt
            )
            if self.memory is not None:
                self.memory.stage(task_id, "candidates", len(last_candidates))
                self.memory.stage(task_id, "verdicts", len(last_verdicts))

            winner = self._pick_winner(last_candidates, last_verdicts)
            if winner is None:
                continue

            qa_report = self.qa_node.check(task_id, winner)
            self.storage.save_checkpoint(
                task_id,
                "qa",
                {
                    "artifact_id": winner.artifact_id,
                    "passed": qa_report.passed,
                    "reason": qa_report.reason,
                },
                attempt,
            )
            if qa_report.passed:
                self.storage.update_task_status(task_id, "completed")
                if self.memory is not None:
                    self.memory.record_decision(
                        task_id=task_id,
                        prompt=prompt,
                        winner=winner.author,
                        status="completed",
                        reason=qa_report.reason,
                    )
                    self.memory.clear_run(task_id)
                self._cleanup_losers(last_candidates, winner)
                return RoundResult(
                    task_id=task_id,
                    candidates=last_candidates,
                    verdicts=last_verdicts,
                    final_status="completed",
                    winner=winner,
                    qa_report=qa_report,
                )

        self.storage.update_task_status(task_id, "needs_human")
        return RoundResult(
            task_id=task_id,
            candidates=last_candidates,
            verdicts=last_verdicts,
            final_status="needs_human",
            winner=None,
        )

    def resume(self, task_id: str, force_research: bool = False) -> RoundResult:
        task = self.storage.get_task(task_id)
        if not task:
            raise ValueError(f"task {task_id!r} not found")
        payload = task.get("payload") or {}
        prompt = payload.get("prompt") or f"Resume task {task_id}"
        if force_research:
            payload.pop("enriched_prompt", None)
        return self.run(prompt, payload)

    @staticmethod
    def _pick_winner(
        candidates: List[Candidate], verdicts: List[ArenaVerdict]
    ) -> Optional[Candidate]:
        approved = {
            verdict.artifact_id
            for verdict in verdicts
            if verdict.status in {"approved", "approve"}
        }
        for candidate in candidates:
            if candidate.artifact_id in approved:
                return candidate
        return None

    @staticmethod
    def _cleanup_losers(candidates: List[Candidate], winner: Candidate) -> None:
        for candidate in candidates:
            if candidate.artifact_id == winner.artifact_id or not candidate.workdir:
                continue
            path = Path(candidate.workdir)
            if path.exists():
                shutil.rmtree(path)

    @staticmethod
    def _await(value: Any) -> Any:
        if not inspect.isawaitable(value):
            return value
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(value)
        import nest_asyncio

        nest_asyncio.apply()
        return loop.run_until_complete(value)
