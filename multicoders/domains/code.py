"""Code domain profile: ast.parse filter + security/compile/doctest validators."""
from __future__ import annotations

import ast
import doctest
import io
import types
from contextlib import redirect_stdout

from ..qa import SecurityScanner
from .base import ValidationOutcome


class CodeProfile:
    """Python code artifacts (the original Multicoders domain)."""

    name = "code"
    artifact_kind = "python"

    def objective_filter(self, content: str) -> tuple[bool, str]:
        text = (content or "").strip()
        if not text:
            return False, "empty artifact"
        try:
            ast.parse(text)
        except SyntaxError as exc:
            return False, f"syntax error: {exc}"
        return True, "parses"

    def validate(self, content: str, *, workdir: str | None = None) -> ValidationOutcome:
        text = content or ""
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            return ValidationOutcome(False, f"ast parse error: {exc}")

        security = SecurityScanner()
        security.visit(tree)
        if security.dangerous_calls:
            return ValidationOutcome(
                False, f"security risk detected: {', '.join(security.dangerous_calls)}"
            )

        try:
            module = compile(text, "<artifact>", "exec")
        except SyntaxError as exc:
            return ValidationOutcome(False, f"compile error: {exc}")

        if ">>>" in text:
            try:
                artifact_module = types.ModuleType("artifact_under_test")
                exec(module, artifact_module.__dict__)
            except Exception as exc:  # noqa: BLE001
                return ValidationOutcome(False, f"import-time error: {exc!r}")
            runner = doctest.DocTestRunner(verbose=False)
            buf = io.StringIO()
            with redirect_stdout(buf):
                for test in doctest.DocTestFinder().find(artifact_module):
                    runner.run(test)
            if runner.failures:
                return ValidationOutcome(False, f"doctest failures: {runner.failures}")

        return ValidationOutcome(True, "qa ok")
