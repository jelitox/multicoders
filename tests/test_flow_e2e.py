"""End-to-end smoke test for the Dispatcher -> Arena -> Tracker pipeline."""
from __future__ import annotations

import os
import tempfile
from typing import Dict, Tuple

from multicoders.arena import Arena
from multicoders.dispatcher import Dispatcher
from multicoders.flow import MulticodersFlow
from multicoders.storage import Storage


CLEAN_CODE = """def add(a, b):
    return a + b
"""

BROKEN_CODE = "def add(a, b):\n    return a +"  # SyntaxError

UGLY_CODE = "x=1\n"  # parses, but a strict judge will reject


def _approve_if_has_def(artifact: Dict) -> Tuple[str, str]:
    content = artifact.get("content", "")
    if "def " in content:
        return "approve", "has a function definition"
    return "reject", "no function found"


def _approve_if_long(artifact: Dict) -> Tuple[str, str]:
    content = artifact.get("content", "")
    if len(content) > 10:
        return "approve", "non-trivial size"
    return "reject", "too short"


def _build_flow(tmp_db: str, coders) -> MulticodersFlow:
    storage = Storage(db_path=tmp_db)
    dispatcher = Dispatcher(storage=storage, coders=coders)
    arena = Arena(
        storage=storage,
        judges={
            "claude": _approve_if_has_def,
            "gemini": _approve_if_long,
        },
    )
    return MulticodersFlow(
        storage=storage, dispatcher=dispatcher, arena=arena, max_retries=1
    )


def test_happy_path_produces_winner() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "mc.db")
        flow = _build_flow(
            db_path,
            coders={
                "claude": lambda _p: CLEAN_CODE,
                "gemini": lambda _p: CLEAN_CODE,
            },
        )
        result = flow.run("write a function that adds two numbers")
        assert result.final_status == "completed"
        assert result.winner is not None
        assert "def add" in result.winner.content


def test_syntax_error_is_filtered_out() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "mc.db")
        flow = _build_flow(
            db_path,
            coders={
                "claude": lambda _p: BROKEN_CODE,
                "gemini": lambda _p: BROKEN_CODE,
            },
        )
        result = flow.run("write something")
        assert result.final_status == "needs_human"
        assert result.winner is None
        assert all(v.status == "filtered_out" for v in result.verdicts)


def test_no_autovote_then_rejected_when_judge_disapproves() -> None:
    """Author == 'claude' so claude won't vote on its own artifact;
    only 'gemini' votes. UGLY_CODE is short -> gemini rejects."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "mc.db")
        flow = _build_flow(
            db_path,
            coders={"claude": lambda _p: UGLY_CODE},
        )
        result = flow.run("anything")
        assert result.final_status == "needs_human"
        rejected = [v for v in result.verdicts if v.status == "rejected"]
        assert rejected, "expected a rejection"
        # Claude must not have voted on its own artifact.
        summary = flow.storage.get_task_summary(result.task_id)
        judges_used = {v["judge"] for v in summary["verdicts"]}
        assert "claude" not in judges_used


def test_parrot_dependency_importable() -> None:
    """We declared ai-parrot as a dependency; smoke-import the public
    flow surface so a missing/broken install fails fast."""
    from parrot.bots.flow import (  # noqa: F401
        AgentsFlow,
        DecisionFlowNode,
        DecisionMode,
        FlowNode,
    )
