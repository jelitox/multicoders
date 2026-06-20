"""Arena: where different agent perspectives compete and find consensus.

This module provides the Arena class, which orchestrates multiple judging 
perspectives to select the best candidate artifact.
"""
from __future__ import annotations

import logging
import ast
import inspect
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from .dispatcher import Candidate
from .storage import Storage


@dataclass
class ArenaVerdict:
    """The result of a single judge's evaluation of an artifact."""
    artifact_id: int
    status: str  # "approved", "rejected"
    judge: str = ""
    feedback: str = ""
    approvals: int = 0
    rejections: int = 0


class ParrotJudge:
    """Judge that uses a Parrot agent to evaluate artifacts."""
    def __init__(self, name: str, focus: str = "overall code quality"):
        from parrot.bots.agent import BasicAgent
        self.name = name
        self.agent = BasicAgent(
            name=name,
            agent_id=name.lower(),
            system_prompt=(
                f"You are {name}, an expert judge focusing on {focus}.\n"
                "Evaluate the provided Python code and return 'approved' or 'rejected' "
                "followed by a short technical explanation."
            ),
            use_tools=False,
        )

    async def judge(self, artifact_content: str) -> Tuple[str, str]:
        """Returns (status, feedback). status should be 'approved' or 'rejected'."""
        try:
            response = await self.agent.ask(f"Evaluate this code:\n\n{artifact_content}")
            content = str(response.content).strip().lower()
            status = "approved" if "approved" in content else "rejected"
            return status, content
        except Exception as e:
            return "rejected", f"Judge failure: {str(e)}"


class CallableJudge:
    def __init__(self, name: str, fn: Callable[[Dict[str, Any]], Tuple[str, str]]):
        self.name = name
        self._fn = fn

    async def judge_artifact(self, artifact: Dict[str, Any]) -> Tuple[str, str]:
        result = self._fn(artifact)
        if inspect.isawaitable(result):
            result = await result
        vote, feedback = result
        return str(vote), str(feedback)


class Arena:
    """The Arena orchestrates multiple judges to evaluate candidates."""

    def __init__(
        self,
        storage: Storage,
        judges: Optional[
            Mapping[str, Callable[[Dict[str, Any]], Tuple[str, str]]]
            | Iterable[Any]
        ] = None,
        profile: Optional[Any] = None,
    ):
        self.storage = storage
        self.judges = self._normalize_judges(judges) if judges else [
            ParrotJudge("Security-Judge", "security and best practices"),
            ParrotJudge("Logic-Judge", "algorithmic correctness and efficiency")
        ]
        # Optional DomainProfile drives the objective filter; defaults to the
        # built-in Python ast.parse gate when None (code domain).
        self.profile = profile
        self.logger = logging.getLogger("multicoders.arena")

    @staticmethod
    def _normalize_judges(
        judges: Mapping[str, Callable[[Dict[str, Any]], Tuple[str, str]]]
        | Iterable[Any],
    ) -> List[Any]:
        if isinstance(judges, Mapping):
            return [CallableJudge(name, fn) for name, fn in judges.items()]
        return list(judges)

    def objective_filter(self, candidate: Candidate) -> bool:
        content = (candidate.content or "").strip()
        if self.profile is not None:
            ok, _reason = self.profile.objective_filter(content)
            self.storage.update_artifact_filter_status(candidate.artifact_id, ok)
            return ok
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

    async def run(self, task_id: str, candidates: List[Candidate]) -> List[ArenaVerdict]:
        """Runs the judging process and finds consensus."""
        self.logger.info(f"Arena: Judging {len(candidates)} candidates for task {task_id}")
        
        all_verdicts: List[ArenaVerdict] = []
        for candidate in candidates:
            if not self.objective_filter(candidate):
                all_verdicts.append(
                    ArenaVerdict(
                        artifact_id=candidate.artifact_id,
                        status="filtered_out",
                        judge="arena",
                        feedback="objective filter failed",
                    )
                )
                continue

            approvals = 0
            rejections = 0
            active_judges = 0
            for judge in self.judges:
                if getattr(judge, "name", None) == candidate.author:
                    continue

                active_judges += 1
                artifact = {
                    "artifact_id": candidate.artifact_id,
                    "author": candidate.author,
                    "content": candidate.content,
                    "workdir": candidate.workdir,
                }
                if hasattr(judge, "judge_artifact"):
                    raw_status, feedback = await judge.judge_artifact(artifact)
                else:
                    raw_status, feedback = await judge.judge(candidate.content)
                is_approved = str(raw_status).lower() in {"approve", "approved"}
                vote = "approve" if is_approved else "reject"
                self.storage.add_verdict(
                    task_id=task_id,
                    judge=judge.name,
                    artifact_id=candidate.artifact_id,
                    vote=vote,
                    reasoning=feedback,
                )
                if is_approved:
                    approvals += 1
                else:
                    rejections += 1
                
                self.storage.save_checkpoint(task_id, f"arena_{judge.name}", {
                    "artifact_id": candidate.artifact_id,
                    "status": vote
                })

            if active_judges > 0 and approvals > active_judges / 2:
                self.logger.info(f"Candidate {candidate.artifact_id} reached consensus.")
                status = "approved"
            else:
                self.logger.warning(f"Candidate {candidate.artifact_id} failed consensus.")
                status = "rejected"

            all_verdicts.append(
                ArenaVerdict(
                    artifact_id=candidate.artifact_id,
                    status=status,
                    judge="arena",
                    feedback=f"{approvals} approvals, {rejections} rejections",
                    approvals=approvals,
                    rejections=rejections,
                )
            )

        return all_verdicts
