from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import pytest

from multicoders.advisory import (
    AdvisoryError,
    AdvisoryRequest,
    apply_dispositions,
    run_advisory,
)
from multicoders.providers import ProviderError, ProviderResult


def _request(**overrides):
    content = "approved requirement"
    payload = {
        "mode": "review",
        "requirement": "Review the change",
        "question": "What could fail?",
        "providers": ["codex", "claude", "gemini"],
        "minimum_quorum": 2,
        "evidence": [
            {
                "locator": "diff",
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
                "content": content,
            }
        ],
        "constraints": [{"source": "SPEC-001", "quote": "Never bill silently."}],
        "coordinator_reasoning": "I think this is already perfect.",
    }
    payload.update(overrides)
    return AdvisoryRequest.from_dict(payload)


def test_neutral_brief_excludes_coordinator_reasoning():
    brief = _request().neutral_brief()
    serialized = json.dumps(brief)
    assert "already perfect" not in serialized
    assert brief["evidence"][0]["content"] == "approved requirement"


def test_evidence_hash_mismatch_fails_closed():
    with pytest.raises(AdvisoryError, match="hash mismatch"):
        _request(
            evidence=[
                {"locator": "diff", "sha256": "wrong", "content": "content"}
            ]
        )


def test_phase_a_runs_concurrently_and_synthesizes_after_barrier(tmp_path):
    barrier = threading.Barrier(3, timeout=2)
    calls: list[tuple[str, str]] = []
    lock = threading.Lock()

    def runner(**kwargs):
        provider = kwargs["provider_name"]
        with lock:
            calls.append((provider, kwargs["sandbox_mode"]))
        barrier.wait()
        payload = {
            "session_id": f"s-{provider}",
            "response": json.dumps(
                {
                    "findings": [
                        {
                            "summary": "Missing timeout",
                            "category": "reliability",
                            "severity": "high",
                            "evidence": ["diff"],
                            "confidence": 0.9,
                            "recommendation": "Add timeout",
                        }
                    ]
                }
            ),
        }
        return ProviderResult(provider, json.dumps(payload), "")

    result = run_advisory(_request(), tmp_path, runner=runner, run_id="run-test")

    assert result["status"] == "completed"
    assert result["quorum"]["completed"] == 3
    assert sorted(calls) == [
        ("claude", "read-only"),
        ("codex", "read-only"),
        ("gemini", "read-only"),
    ]
    assert result["synthesis"]["agreements"][0]["providers"] == [
        "claude",
        "codex",
        "gemini",
    ]
    assert {
        item["disposition"] for item in result["synthesis"]["findings"]
    } == {"ESCALATE"}


def test_quorum_failure_withholds_synthesis(tmp_path):
    def runner(**kwargs):
        if kwargs["provider_name"] != "codex":
            raise ProviderError("offline")
        return ProviderResult("codex", '{"response":"one opinion"}', "")

    result = run_advisory(_request(), tmp_path, runner=runner)
    assert result["status"] == "needs_human"
    assert result["quorum"]["met"] is False
    assert result["synthesis"]["findings"] if "findings" in result["synthesis"] else True
    assert "withheld" in result["synthesis"]["summary"]


def test_dispositions_require_known_finding_and_reason(tmp_path):
    result = run_advisory(_request(), tmp_path, dry_run=True)
    finding = result["synthesis"]["findings"][0]["finding_id"]
    updated = apply_dispositions(
        result, {finding: ("CONFIRM", "Supported by the approved spec")}
    )
    assert updated["synthesis"]["findings"][0]["disposition"] == "CONFIRM"
    with pytest.raises(AdvisoryError, match="unknown"):
        apply_dispositions(result, {"missing": ("REJECT", "not applicable")})
