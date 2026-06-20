"""Tests for the provider-agnostic AgentBackend layer (Fase 3)."""
from __future__ import annotations

import asyncio
import os

import pytest

from multicoders.backends import (
    AgentContext,
    ApiBackend,
    BackendCoder,
    BackendJudge,
    BackendUnavailable,
    CliBackend,
    MockBackend,
    TaskSpec,
    default_mock_backends,
    is_billed,
    order_backends,
    select_backends,
)


def test_mock_backend_generates_code_and_review():
    backend = MockBackend("Tester")
    code = asyncio.run(backend.generate(TaskSpec(prompt="add", kind="code"), AgentContext()))
    assert code.backend == "Tester"
    assert "def solution" in code.content
    review = asyncio.run(backend.generate(TaskSpec(prompt="x", kind="review"), AgentContext()))
    assert "approved" in review.content.lower()
    assert backend.capabilities() == {"code", "review"}
    assert asyncio.run(backend.health()) is True


def test_backend_coder_returns_plain_text():
    coder = BackendCoder(MockBackend("Claude", "def f():\n    return 1\n"))
    assert coder.name == "Claude"
    out = asyncio.run(coder.generate("whatever"))
    assert out == "def f():\n    return 1\n"


def test_backend_judge_parses_vote():
    approver = BackendJudge(MockBackend("J", "approved: looks good"))
    status, _ = asyncio.run(approver.judge("code"))
    assert status == "approved"

    rejecter = BackendJudge(MockBackend("J", "rejected: nope"))
    status, _ = asyncio.run(rejecter.judge("code"))
    assert status == "rejected"


def test_order_backends_respects_preference():
    a, b, c = MockBackend("a"), MockBackend("b"), MockBackend("c")
    ordered = order_backends([a, b, c], ["c", "a"])
    assert [x.name for x in ordered] == ["c", "a", "b"]


def test_select_backends_filters_by_capability_and_minimum():
    coders = default_mock_backends()
    healthy = asyncio.run(select_backends(coders, capability="code", minimum=2))
    assert len(healthy) == 2

    with pytest.raises(BackendUnavailable):
        asyncio.run(select_backends(coders, capability="nonexistent", minimum=1))


def test_unhealthy_backend_is_excluded():
    good = MockBackend("good")
    bad = MockBackend("bad", healthy=False)
    healthy = asyncio.run(select_backends([good, bad], minimum=1))
    assert [b.name for b in healthy] == ["good"]


def test_cli_backend_unavailable_when_binary_missing():
    backend = CliBackend("definitely-not-a-real-cli")
    assert asyncio.run(backend.health()) is False
    with pytest.raises(BackendUnavailable):
        asyncio.run(backend.generate(TaskSpec(prompt="x"), AgentContext()))


def test_cli_backend_strips_api_keys_byo_auth(monkeypatch):
    # HARD CONSTRAINT: never let a provider API key reach the official CLI.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-be-stripped")
    backend = CliBackend("claude")
    backend._guard_byo_auth()
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_api_backend_is_billed_and_others_are_not():
    assert is_billed(ApiBackend("a", "role")) is True
    assert is_billed(MockBackend("m")) is False
