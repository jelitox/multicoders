"""QA Node: post-Arena gate that validates the winning artifact.

The Arena says "two judges liked it". The QA Node says "and it actually
runs". It compiles the code, optionally runs embedded doctests, and
records the outcome as a verdict authored by ``qa`` so the audit trail
includes a non-LLM signal.
"""
from __future__ import annotations

import doctest
import io
from contextlib import redirect_stdout
from dataclasses import dataclass
from typing import Optional

from .dispatcher import Candidate
from .storage import Storage


@dataclass
class QAReport:
    artifact_id: int
    passed: bool
    reason: str


class QANode:
    def __init__(self, storage: Storage, run_doctests: bool = True) -> None:
        self.storage = storage
        self.run_doctests = run_doctests

    def check(self, task_id: str, candidate: Candidate) -> QAReport:
        content = candidate.content or ""

        try:
            module = compile(content, f"<artifact-{candidate.artifact_id}>", "exec")
        except SyntaxError as exc:
            return self._record(task_id, candidate.artifact_id, False, f"compile error: {exc}")

        if self.run_doctests and ">>>" in content:
            try:
                ns: dict = {}
                exec(module, ns)
            except Exception as exc:
                return self._record(
                    task_id, candidate.artifact_id, False, f"import-time error: {exc!r}"
                )
            finder = doctest.DocTestFinder()
            runner = doctest.DocTestRunner(verbose=False)
            buf = io.StringIO()
            with redirect_stdout(buf):
                for test in finder.find(ns, "<artifact>"):
                    runner.run(test)
            if runner.failures:
                return self._record(
                    task_id,
                    candidate.artifact_id,
                    False,
                    f"doctest failures: {runner.failures}",
                )

        return self._record(task_id, candidate.artifact_id, True, "qa ok")

    def _record(
        self, task_id: str, artifact_id: int, passed: bool, reason: str
    ) -> QAReport:
        self.storage.add_verdict(
            task_id=task_id,
            judge="qa",
            artifact_id=artifact_id,
            vote="approve" if passed else "reject",
            reasoning=reason,
        )
        return QAReport(artifact_id=artifact_id, passed=passed, reason=reason)
