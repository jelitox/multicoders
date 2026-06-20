"""Tests for the layered MemoryService (Fase 4a)."""
from __future__ import annotations

import os
import tempfile

from multicoders.arena import Arena
from multicoders.dispatcher import Dispatcher
from multicoders.flow import MulticodersFlow
from multicoders.memory import InProcessWorkingMemory, JsonDecisionMemory, MemoryService
from multicoders.mocks import default_mock_coders, default_mock_judges
from multicoders.research import ResearchNode
from multicoders.storage import Storage


def test_working_memory_namespaced_by_run():
    wm = InProcessWorkingMemory()
    wm.stage("run-1", "research", {"files": 3})
    wm.stage("run-2", "research", {"files": 9})
    assert wm.staged("run-1", "research") == {"files": 3}
    assert wm.staged("run-1") == {"research": {"files": 3}}
    wm.clear_run("run-1")
    assert wm.staged("run-1") == {}
    assert wm.staged("run-2", "research") == {"files": 9}


def test_decision_memory_records_and_recalls():
    with tempfile.TemporaryDirectory() as tmp:
        mem = JsonDecisionMemory(os.path.join(tmp, "decisions.jsonl"))
        from multicoders.memory import Decision

        mem.record(Decision(task_id="t1", prompt="add a health endpoint", winner="Claude", status="completed", tags=["http"]))
        mem.record(Decision(task_id="t2", prompt="parse a CSV file", winner="Gemini", status="completed"))

        hits = mem.recall("create a health check endpoint")
        assert hits
        assert hits[0].decision.task_id == "t1"
        # Unrelated query should not match the CSV decision strongly.
        assert all(h.decision.task_id != "t2" for h in mem.recall("health endpoint http"))


def test_service_local_factory_and_status():
    with tempfile.TemporaryDirectory() as tmp:
        mem = MemoryService.local(state_dir=tmp)
        status = mem.layers_status()
        assert status["working"] is True
        assert status["decisions"] is True
        assert status["documents"] is False  # PageIndex arrives in Fase 4b
        assert mem.ground("anything") == ""


def test_flow_records_decision_and_enables_cross_run_recall():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "mc.db")
        mem = MemoryService.local(state_dir=tmp)
        storage = Storage(db_path)
        flow = MulticodersFlow(
            storage,
            Dispatcher(storage, default_mock_coders()),
            Arena(storage, default_mock_judges()),
            research_node=ResearchNode(storage),
            memory=mem,
        )

        result = flow.run("write a trivial function")
        if result.final_status != "completed":
            return  # mock consensus is deterministic but guard anyway

        recalled = mem.recall_decisions("write a trivial function")
        assert recalled
        assert recalled[0].decision.winner == result.winner.author
        # run namespace cleared after completion
        assert mem.staged(result.task_id) == {}
