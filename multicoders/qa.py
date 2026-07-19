"""QA Node: post-Arena gate that validates the winning artifact.

The Arena says "two judges liked it". The QA Node says "and it actually
runs". It compiles the code, optionally runs embedded doctests, and
records the outcome as a verdict authored by ``qa`` so the audit trail
includes a non-LLM signal.
"""
from __future__ import annotations

import doctest
import io
import os
import subprocess
import sys
import types
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import ast
from .dispatcher import Candidate
from .storage import Storage


@dataclass
class QAReport:
    artifact_id: int
    passed: bool
    reason: str


class SecurityScanner(ast.NodeVisitor):
    def __init__(self) -> None:
        self.dangerous_calls: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            if node.func.id in ("eval", "exec", "compile"):
                self.dangerous_calls.append(f"Dangerous built-in call: {node.func.id}")
        elif isinstance(node.func, ast.Attribute):
            if node.func.attr == "system" and isinstance(node.func.value, ast.Name) and node.func.value.id == "os":
                self.dangerous_calls.append("Dangerous call: os.system")
            if node.func.attr == "Popen" and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess":
                for keyword in node.keywords:
                    if keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                        self.dangerous_calls.append("Dangerous call: subprocess.Popen(shell=True)")
        self.generic_visit(node)


class StaticAnalyzer(ast.NodeVisitor):
    def __init__(self) -> None:
        self.issues: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # Check for empty functions that are not intended to be empty
        if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
            if not (node.name.startswith("_") or any(isinstance(decorator, ast.Name) and decorator.id == "abstractmethod" for decorator in node.decorator_list)):
                self.issues.append(f"Function '{node.name}' contains only 'pass' but is not marked as abstract/private.")

        # Check for missing return type hint on non-init methods
        if node.name != "__init__" and node.returns is None:
            self.issues.append(f"Function '{node.name}' is missing a return type hint.")

        self.generic_visit(node)


class QANode:
    def __init__(
        self,
        storage: Storage,
        run_doctests: bool = True,
        pytest_timeout: int = 30,
        strict_static: bool = False,
    ) -> None:
        self.storage = storage
        self.run_doctests = run_doctests
        self.pytest_timeout = pytest_timeout
        self.strict_static = strict_static

    def check(self, task_id: str, candidate: Candidate) -> QAReport:
        content = candidate.content or ""

        # 1. Static Analysis & Security Scan
        try:
            tree = ast.parse(content)

            # Security first
            security = SecurityScanner()
            security.visit(tree)
            if security.dangerous_calls:
                return self._record(task_id, candidate.artifact_id, False, f"security risk detected: {', '.join(security.dangerous_calls)}")

            analyzer = StaticAnalyzer()
            analyzer.visit(tree)
            if analyzer.issues:
                issue_msg = " | ".join(analyzer.issues)
                if self.strict_static:
                    return self._record(task_id, candidate.artifact_id, False, f"static analysis failed: {issue_msg}")
                else:
                    # Log issues but continue if not strict
                    print(f"QA Warning: {issue_msg}")
        except Exception as exc:
            return self._record(task_id, candidate.artifact_id, False, f"ast parse error: {exc}")

        # 2. Compilation check
        try:
            module = compile(content, f"<artifact-{candidate.artifact_id}>", "exec")
        except SyntaxError as exc:
            return self._record(task_id, candidate.artifact_id, False, f"compile error: {exc}")

        workdir = candidate.workdir
        if workdir and Path(workdir).is_dir() and self._has_tests(workdir):
            return self._run_pytest(task_id, candidate.artifact_id, workdir)

        if self.run_doctests and ">>>" in content:
            try:
                artifact_module = types.ModuleType(f"artifact_{candidate.artifact_id}")
                exec(module, artifact_module.__dict__)
            except Exception as exc:
                return self._record(
                    task_id, candidate.artifact_id, False, f"import-time error: {exc!r}"
                )
            finder = doctest.DocTestFinder()
            runner = doctest.DocTestRunner(verbose=False)
            buf = io.StringIO()
            with redirect_stdout(buf):
                for test in finder.find(artifact_module, "<artifact>"):
                    runner.run(test)
            if runner.failures:
                return self._record(
                    task_id,
                    candidate.artifact_id,
                    False,
                    f"doctest failures: {runner.failures}",
                )

        return self._record(task_id, candidate.artifact_id, True, "qa ok")

    def _has_tests(self, workdir: str) -> bool:
        root = Path(workdir)
        for path in root.rglob("test_*.py"):
            return True
        for path in root.rglob("*_test.py"):
            return True
        return False

    def _run_pytest(self, task_id: str, artifact_id: int, workdir: str) -> QAReport:
        env = os.environ.copy()
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", workdir],
                capture_output=True,
                text=True,
                timeout=self.pytest_timeout,
                env=env,
            )
        except FileNotFoundError:
            return self._record(task_id, artifact_id, False, "pytest unavailable")
        except subprocess.TimeoutExpired:
            return self._record(
                task_id, artifact_id, False, f"pytest timeout after {self.pytest_timeout}s"
            )

        tail = (result.stdout or "")[-800:] + (result.stderr or "")[-400:]
        passed = result.returncode == 0
        reason = f"pytest rc={result.returncode}: {tail.strip()}"
        return self._record(task_id, artifact_id, passed, reason)

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
