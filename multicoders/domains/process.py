"""Non-code domain profile: administrative / ops process definitions.

Proof that the orchestrator generalizes beyond code (SPEC §3.3, Fase 5). An
artifact here is a JSON process definition — e.g. an administrative workflow —
not Python. The objective filter is JSON well-formedness + required structure;
validation is schema + business-rule checks, with an HITL flag for steps that
require human approval.
"""
from __future__ import annotations

import json
from typing import Any

from .base import ValidationOutcome


class ProcessProfile:
    """Validate JSON process/workflow artifacts (administrative, ops, ...)."""

    name = "process"
    artifact_kind = "json"

    def __init__(
        self,
        *,
        required_fields: list[str] | None = None,
        require_steps: bool = True,
    ) -> None:
        self.required_fields = required_fields or ["title", "steps"]
        self.require_steps = require_steps

    def _parse(self, content: str) -> Any:
        return json.loads((content or "").strip())

    def objective_filter(self, content: str) -> tuple[bool, str]:
        text = (content or "").strip()
        if not text:
            return False, "empty artifact"
        try:
            data = self._parse(text)
        except json.JSONDecodeError as exc:
            return False, f"invalid JSON: {exc}"
        if not isinstance(data, dict):
            return False, "process definition must be a JSON object"
        return True, "valid JSON object"

    def validate(self, content: str, *, workdir: str | None = None) -> ValidationOutcome:
        try:
            data = self._parse(content)
        except json.JSONDecodeError as exc:
            return ValidationOutcome(False, f"invalid JSON: {exc}")
        if not isinstance(data, dict):
            return ValidationOutcome(False, "process definition must be a JSON object")

        missing = [f for f in self.required_fields if f not in data]
        if missing:
            return ValidationOutcome(False, f"missing required fields: {', '.join(missing)}")

        steps = data.get("steps")
        if self.require_steps:
            if not isinstance(steps, list) or not steps:
                return ValidationOutcome(False, "process must define a non-empty 'steps' list")
            for i, step in enumerate(steps):
                if not isinstance(step, dict) or not step.get("action"):
                    return ValidationOutcome(False, f"step {i} is missing an 'action'")

        needs_human = bool(
            isinstance(steps, list)
            and any(isinstance(s, dict) and s.get("requires_approval") for s in steps)
        )
        reason = "process valid" + (" (pending human approval)" if needs_human else "")
        return ValidationOutcome(True, reason, needs_human=needs_human)
