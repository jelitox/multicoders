"""Tests for the QA Node post-Arena gate."""
from __future__ import annotations

import os
import tempfile
from typing import Dict, Tuple

from multicoders.arena import Arena
from multicoders.dispatcher import Dispatcher
from multicoders.flow import MulticodersFlow
from multicoders.qa import QANode
from multicoders.storage import Storage


def _approve_all(_artifact: Dict) -> Tuple[str, str]:
    return "approve", "ok"


def _build_flow(tmp_db: str, coders) -> MulticodersFlow:
    storage = Storage(db_path=tmp_db)
    dispatcher = Dispatcher(storage=storage, coders=coders)
    arena = Arena(
        storage=storage,
        judges={"claude": _approve_all, "gemini": _approve_all, "codex": _approve_all},
    )
    return MulticodersFlow(
        storage=storage,
        dispatcher=dispatcher,
        arena=arena,
        qa_node=QANode(storage),
        max_retries=1,
    )


VALID_DOCTEST = '''
def add(a, b):
    """
    >>> add(2, 3)
    5
    """
    return a + b
'''

BROKEN_DOCTEST = '''
def add(a, b):
    """
    >>> add(2, 3)
    99
    """
    return a + b
'''

NO_DOCTEST = "def add(a, b):\n    return a + b\n"


def test_qa_passes_valid_doctest() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "mc.db")
        flow = _build_flow(db_path, coders={"claude": lambda _p: VALID_DOCTEST})
        result = flow.run("add two numbers")
        assert result.final_status == "completed"
        assert result.qa_report is not None
        assert result.qa_report.passed


def test_qa_rejects_failing_doctest() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "mc.db")
        flow = _build_flow(db_path, coders={"claude": lambda _p: BROKEN_DOCTEST})
        result = flow.run("add two numbers")
        assert result.final_status == "needs_human"
        summary = flow.storage.get_task_summary(result.task_id)
        qa_verdicts = [v for v in summary["verdicts"] if v["judge"] == "qa"]
        assert qa_verdicts, "QA Node must record at least one verdict"
        assert all(v["vote"] == "reject" for v in qa_verdicts)


def test_qa_skipped_when_no_doctest_runs_clean() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "mc.db")
        flow = _build_flow(db_path, coders={"claude": lambda _p: NO_DOCTEST})
        result = flow.run("add two numbers")
        assert result.final_status == "completed"
        assert result.qa_report is not None
        assert result.qa_report.passed
        assert result.qa_report.reason == "qa ok"
