"""Smoke test for --dry-run path: full DAG without LLM calls."""
from __future__ import annotations

import os
import tempfile

from multicoders.arena import Arena
from multicoders.dispatcher import Dispatcher
from multicoders.flow import MulticodersFlow
from multicoders.mocks import default_mock_coders, default_mock_judges
from multicoders.research import ResearchNode
from multicoders.storage import Storage


def test_dry_run_end_to_end():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.db")
        storage = Storage(db_path)
        dispatcher = Dispatcher(storage, default_mock_coders())
        arena = Arena(storage, default_mock_judges())
        research = ResearchNode(storage)
        flow = MulticodersFlow(storage, dispatcher, arena, research_node=research)

        result = flow.run("Trivial task")

        assert result.final_status in {"completed", "needs_human"}
        if result.final_status == "completed":
            assert result.winner is not None
            assert result.winner.author in {"Claude", "Gemini"}


def test_config_detects_missing_providers(monkeypatch):
    from multicoders.config import detect_providers, PROVIDER_ENV

    for env in PROVIDER_ENV.values():
        monkeypatch.delenv(env, raising=False)

    status = detect_providers()
    assert status.available == []
    assert set(status.missing) == set(PROVIDER_ENV.keys())
