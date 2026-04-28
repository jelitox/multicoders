"""Arena: objective filter + Tribunal of Two judges using real Parrot Agents.

Phase 1 (objective): each candidate must parse as Python via ast.parse.
Phase 2 (subjective): two judges vote approve/reject, skipping the
candidate's own author (no autovoto). Two approvals = approved; any
rejection sends it back.
"""
from __future__ import annotations

import ast
import asyncio
from dataclasses import dataclass
from typing import Dict, List, Tuple

from parrot.bots.agent import BasicAgent
from .dispatcher import Candidate
from .storage import Storage


@dataclass
class ArenaVerdict:
    artifact_id: int
    status: str  # 'approved' | 'rejected' | 'filtered_out'
    approvals: int
    rejections: int


class ParrotJudge:
    def __init__(self, name: str):
        self.name = name
        self.agent = BasicAgent(
            name=name,
            agent_id=f"judge_{name.lower()}",
            system_prompt=(
                f"You are {name}, a senior code reviewer. "
                "You will receive a Python code snippet. "
                "Respond with 'APPROVE' if it is correct and follows best practices, "
                "or 'REJECT' followed by a brief reason if it has issues. "
                "Format: VOTE: <APPROVE/REJECT>\nREASON: <reason>"
            )
        )

    async def judge(self, artifact_content: str) -> Tuple[str, str]:
        response = await self.agent.ask(f"Review this code:\n\n{artifact_content}")
        text = str(response.content)
        vote = "approve" if "VOTE: APPROVE" in text.upper() else "reject"
        reason = text.split("REASON:")[-1].strip() if "REASON:" in text else "No reason provided"
        return vote, reason


class Arena:
    def __init__(self, storage: Storage, judges: List[ParrotJudge]) -> None:
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

    def judge_candidate(self, task_id: str, candidate: Candidate) -> ArenaVerdict:
        artifact = self.storage.get_artifact(candidate.artifact_id)
        if artifact is None:
            raise ValueError(f"artifact {candidate.artifact_id} not found")

        approvals = 0
        rejections = 0
        loop = asyncio.get_event_loop()

        for judge in self.judges:
            if judge.name == candidate.author:
                continue

            vote, reason = loop.run_until_complete(judge.judge(candidate.content))

            self.storage.add_verdict(
                task_id=task_id,
                judge=judge.name,
                artifact_id=candidate.artifact_id,
                vote=vote,
                reasoning=reason,
            )
            if vote == "approve":
                approvals += 1
            else:
                rejections += 1

        # Consensus logic: need at least 2 approvals and 0 rejections for MVP
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

    def run(self, task_id: str, candidates: List[Candidate]) -> List[ArenaVerdict]:
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

            verdicts.append(self.judge_candidate(task_id, candidate))
        return verdicts

