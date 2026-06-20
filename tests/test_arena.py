"""Tests for the real Arena consensus engine.

These exercise the current ``multicoders.arena.Arena`` API (judges that vote
on candidates with majority consensus), not the obsolete ``ArenaNode`` /
Parrot ``DecisionNode`` wrapper that earlier versions shipped.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Dict, List, Tuple

from multicoders.arena import Arena
from multicoders.dispatcher import Candidate
from multicoders.storage import Storage


VALID_CODE = "def add(a, b):\n    return a + b\n"


def _make_storage_with_candidate(tmp_db: str, author: str) -> Tuple[Storage, str, Candidate]:
    """Create a Storage with one task + one persisted artifact ready to judge."""
    storage = Storage(db_path=tmp_db)
    task_id = "task-arena"
    storage.create_task(task_id, {"prompt": "add two numbers"})
    artifact_id = storage.add_artifact(task_id, author=author, content=VALID_CODE)
    candidate = Candidate(artifact_id=artifact_id, author=author, content=VALID_CODE)
    return storage, task_id, candidate


def test_author_judge_is_skipped() -> None:
    """A judge whose name matches the candidate author must not vote on it."""
    called: List[str] = []

    def _judge(name: str, vote: str):
        def _fn(_artifact: Dict) -> Tuple[str, str]:
            called.append(name)
            return vote, f"{name} says {vote}"

        return _fn

    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "mc.db")
        storage, task_id, candidate = _make_storage_with_candidate(db_path, author="claude")
        arena = Arena(
            storage=storage,
            judges={
                "claude": _judge("claude", "reject"),  # author — should be skipped
                "gemini": _judge("gemini", "approve"),
                "codex": _judge("codex", "approve"),
            },
        )

        verdicts = asyncio.run(arena.run(task_id, [candidate]))

    assert "claude" not in called
    assert set(called) == {"gemini", "codex"}
    verdict = verdicts[0]
    assert verdict.status == "approved"
    assert verdict.approvals == 2
    assert verdict.rejections == 0


def test_majority_rejection_fails_consensus() -> None:
    """With the author skipped, a minority approval must not reach consensus."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "mc.db")
        storage, task_id, candidate = _make_storage_with_candidate(db_path, author="codex")
        arena = Arena(
            storage=storage,
            judges={
                "claude": lambda _a: ("approve", "ok"),
                "gemini": lambda _a: ("reject", "nope"),
                "codex": lambda _a: ("reject", "author vote ignored"),  # author skipped
            },
        )

        verdicts = asyncio.run(arena.run(task_id, [candidate]))

    verdict = verdicts[0]
    # Active judges: claude (approve) + gemini (reject) -> 1/2, not a majority.
    assert verdict.status == "rejected"
    assert verdict.approvals == 1
    assert verdict.rejections == 1


def test_syntax_error_is_filtered_out() -> None:
    """A candidate that fails ``ast.parse`` is filtered before judging."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "mc.db")
        storage = Storage(db_path=db_path)
        task_id = "task-filter"
        storage.create_task(task_id, {"prompt": "broken"})
        broken = "def add(a, b):\n    return a +"
        artifact_id = storage.add_artifact(task_id, author="claude", content=broken)
        candidate = Candidate(artifact_id=artifact_id, author="claude", content=broken)
        arena = Arena(storage=storage, judges={"gemini": lambda _a: ("approve", "ok")})

        verdicts = asyncio.run(arena.run(task_id, [candidate]))

    assert verdicts[0].status == "filtered_out"
