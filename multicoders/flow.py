"""Top-level orchestration: Dispatcher -> Arena -> Tracker.

This is the deterministic backbone the Multicoders MVP runs on. It is
intentionally kept independent of parrot's heavyweight runtime so the
loop is testable without an LLM. The next iteration wraps each step as
a parrot ``FlowNode`` inside an ``AgentsFlow`` DAG (see _refs/).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .arena import Arena, ArenaVerdict
from .dispatcher import Candidate, Dispatcher
from .research import ResearchNode
from .storage import Storage


@dataclass
class RoundResult:
    task_id: str
    candidates: List[Candidate]
    verdicts: List[ArenaVerdict]
    final_status: str
    winner: Optional[Candidate]


class MulticodersFlow:
    def __init__(
        self,
        storage: Storage,
        dispatcher: Dispatcher,
        arena: Arena,
        research_node: Optional[ResearchNode] = None,
        max_retries: int = 2,
    ) -> None:
        self.storage = storage
        self.dispatcher = dispatcher
        self.arena = arena
        self.research_node = research_node or ResearchNode(storage)
        self.max_retries = max_retries

    def run(self, prompt: str, payload: Optional[Dict[str, Any]] = None) -> RoundResult:
        task_id = str(uuid.uuid4())
        
        # 1. Research Phase
        context = self.research_node.enrich(task_id, prompt)
        
        # 2. Create Task with enriched data
        initial_payload = payload or {}
        initial_payload.update({
            "prompt": prompt,
            "enriched_prompt": context.enriched_prompt,
            "metadata": context.metadata
        })
        self.storage.create_task(task_id, initial_payload)

        attempt = 0
        last_candidates: List[Candidate] = []
        last_verdicts: List[ArenaVerdict] = []

        while attempt <= self.max_retries:
            # Use enriched prompt for dispatching
            last_candidates = self.dispatcher.dispatch(task_id, context.enriched_prompt)
            last_verdicts = self.arena.run(task_id, last_candidates)

            winner = self._pick_winner(last_candidates, last_verdicts)
            if winner is not None:
                self.storage.update_task_status(task_id, "completed")
                return RoundResult(
                    task_id=task_id,
                    candidates=last_candidates,
                    verdicts=last_verdicts,
                    final_status="completed",
                    winner=winner,
                )
            attempt += 1

        self.storage.update_task_status(task_id, "needs_human")
        return RoundResult(
            task_id=task_id,
            candidates=last_candidates,
            verdicts=last_verdicts,
            final_status="needs_human",
            winner=None,
        )

    @staticmethod
    def _pick_winner(
        candidates: List[Candidate], verdicts: List[ArenaVerdict]
    ) -> Optional[Candidate]:
        approved = {v.artifact_id for v in verdicts if v.status == "approved"}
        for candidate in candidates:
            if candidate.artifact_id in approved:
                return candidate
        return None
