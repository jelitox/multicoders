"""Fase 4b: PageIndex/GraphIndex layers — feature detection + wiring.

The pinned ai-parrot lacks these modules, so these tests cover the
graceful-degradation path and the wiring against injected fakes (proving the
layers activate correctly once the submodule is bumped).
"""
from __future__ import annotations

import os
import tempfile

from multicoders.memory import (
    Decision,
    GraphIndexEpisodicMemory,
    JsonDecisionMemory,
    MemoryService,
    PageIndexDocumentMemory,
)
from multicoders.research import ResearchNode
from multicoders.storage import Storage


def test_pageindex_inert_without_toolkit():
    layer = PageIndexDocumentMemory(None)
    assert layer.available() is False
    assert layer.ground("anything") == ""


def test_pageindex_grounds_with_injected_toolkit():
    class FakeToolkit:
        async def retrieve(self, tree_name, query, top_k=5):
            return f"[{tree_name}] grounded:{query}"

    layer = PageIndexDocumentMemory(FakeToolkit(), tree_name="repo")
    assert layer.available() is True
    assert layer.ground("health endpoint") == "[repo] grounded:health endpoint"


def test_graphindex_falls_back_to_json_when_unavailable():
    with tempfile.TemporaryDirectory() as tmp:
        fallback = JsonDecisionMemory(os.path.join(tmp, "d.jsonl"))
        episodic = GraphIndexEpisodicMemory(fallback, toolkit=None)
        assert episodic.available() is False
        episodic.record(Decision(task_id="t1", prompt="add health endpoint", winner="Claude", status="completed"))
        hits = episodic.recall("health endpoint")
        assert hits and hits[0].decision.task_id == "t1"


def test_with_knowledge_factory_degrades_gracefully():
    with tempfile.TemporaryDirectory() as tmp:
        mem = MemoryService.with_knowledge(state_dir=tmp, repo=tmp)
        status = mem.layers_status()
        assert status["working"] is True
        assert status["decisions"] is True
        assert isinstance(status["documents"], bool)  # True only after the bump
        # Decision memory works regardless of the PageIndex/GraphIndex bump.
        mem.record_decision(task_id="t1", prompt="parse csv", winner="Gemini", status="completed")
        assert mem.recall_decisions("parse csv")


def test_research_prefers_grounding_when_memory_available():
    class FakeMemory:
        def ground(self, query, top_k=5):
            return "SECTION: health endpoints live in app.py"

    storage = Storage(":memory:")
    node = ResearchNode(storage, memory=FakeMemory())
    ctx = node.enrich("t1", "add a health endpoint")
    assert "Grounded context (PageIndex retrieval)" in ctx.enriched_prompt
    assert "SECTION: health endpoints" in ctx.enriched_prompt
    assert ctx.metadata["grounded"] is True
    assert ctx.metadata["source"] == "pageindex"


def test_research_falls_back_without_memory():
    storage = Storage(":memory:")
    ctx = ResearchNode(storage).enrich("t1", "add a health endpoint")
    assert "Repository Files:" in ctx.enriched_prompt
    assert ctx.metadata["grounded"] is False
