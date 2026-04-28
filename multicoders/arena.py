"""Arena: objective filter + Tribunal of Two judges.

Phase 1 (objective): each candidate must parse as Python via ast.parse.
Phase 2 (subjective): two judges vote approve/reject, skipping the
candidate's own author (no autovoto). Two approvals = approved; any
rejection sends it back.

A judge is a callable ``(artifact_dict) -> (vote, reasoning)``.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple

from .dispatcher import Candidate
from .storage import Storage


JudgeFn = Callable[[Dict], Tuple[str, str]]


@dataclass
class ArenaVerdict:
    artifact_id: int
    status: str  # 'approved' | 'rejected' | 'filtered_out'
    approvals: int
    rejections: int


class Arena:
    def __init__(self, storage: Storage, judges: Dict[str, JudgeFn]) -> None:
        if len(judges) < 2:
            raise ValueError("Tribunal of Two requires at least 2 judges")
        self.storage = storage
        self.judges = judges

    def objective_filter(self, candidate: Candidate) -> bool:
        content = (candidate.content or "").strip()
        if not content:
            self.storage.update_artifact_filter_status(candidate.artifact_id, False)
            return False
        try:
            ast.parse(content)
        except SyntaxError:
            self.storage.update_artifact_filter_status(candidate.artifact_id, False)
            return False
        self.storage.update_artifact_filter_status(candidate.artifact_id, True)
        return True

    def judge(self, task_id: str, candidate: Candidate) -> ArenaVerdict:
        artifact = self.storage.get_artifact(candidate.artifact_id)
        if artifact is None:
            raise ValueError(f"artifact {candidate.artifact_id} not found")

        approvals = 0
        rejections = 0
        for judge_name, judge_fn in self.judges.items():
            if judge_name == candidate.author:
                continue
            vote, reasoning = judge_fn(artifact)
            self.storage.add_verdict(
                task_id=task_id,
                judge=judge_name,
                artifact_id=candidate.artifact_id,
                vote=vote,
                reasoning=reasoning,
            )
            if vote == "approve":
                approvals += 1
            else:
                rejections += 1

        if rejections > 0:
            status = "rejected"
        elif approvals >= 2:
            status = "approved"
        else:
            status = "rejected"

        return ArenaVerdict(
            artifact_id=candidate.artifact_id,
            status=status,
            approvals=approvals,
            rejections=rejections,
        )

    def run(
        self, task_id: str, candidates: List[Candidate]
    ) -> List[ArenaVerdict]:
        verdicts: List[ArenaVerdict] = []
        for candidate in candidates:
            if not self.objective_filter(candidate):
                verdicts.append(
                    ArenaVerdict(
                        artifact_id=candidate.artifact_id,
                        status="filtered_out",
                        approvals=0,
                        rejections=0,
                    )
                )
                continue
            verdicts.append(self.judge(task_id, candidate))
        return verdicts
