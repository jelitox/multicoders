"""Tests for domain generalization (Fase 5): DomainProfile + non-code proof."""
from __future__ import annotations

import json
import os
import tempfile

from multicoders.arena import Arena
from multicoders.dispatcher import Dispatcher
from multicoders.domains import CodeProfile, ProcessProfile, get_profile, list_profiles
from multicoders.flow import MulticodersFlow
from multicoders.research import ResearchNode
from multicoders.storage import Storage


def test_registry_lists_and_builds_profiles():
    assert set(list_profiles()) == {"code", "process"}
    assert isinstance(get_profile("code"), CodeProfile)
    assert isinstance(get_profile("process"), ProcessProfile)
    try:
        get_profile("nope")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_code_profile_filter_and_validate():
    profile = CodeProfile()
    assert profile.objective_filter("def f():\n    return 1\n")[0] is True
    assert profile.objective_filter("def f(:")[0] is False
    assert profile.validate("def f():\n    return 1\n").passed is True
    bad = profile.validate("import os\nos.system('rm -rf /')\n")
    assert bad.passed is False and "security" in bad.reason


def test_process_profile_filter_and_validate():
    profile = ProcessProfile()
    assert profile.objective_filter("not json")[0] is False
    assert profile.objective_filter('{"title": "x"}')[0] is True

    good = json.dumps({"title": "Onboarding", "steps": [{"action": "create account"}]})
    assert profile.validate(good).passed is True

    missing = json.dumps({"steps": [{"action": "x"}]})
    out = profile.validate(missing)
    assert out.passed is False and "title" in out.reason

    needs_human = json.dumps(
        {"title": "Pay", "steps": [{"action": "wire funds", "requires_approval": True}]}
    )
    out = profile.validate(needs_human)
    assert out.passed is True and out.needs_human is True


def test_flow_runs_non_code_process_domain_end_to_end():
    """The orchestrator runs a non-code domain by swapping only the profile."""
    process_doc = json.dumps(
        {"title": "Vacation request", "steps": [{"action": "submit form"}, {"action": "notify manager"}]}
    )
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(os.path.join(tmp, "mc.db"))
        # Coders emit JSON process definitions, not code.
        dispatcher = Dispatcher(storage, {"alice": lambda _p: process_doc, "bob": lambda _p: process_doc})
        # Judges approve; author is skipped automatically.
        judges = {
            "alice": lambda _a: ("approve", "ok"),
            "bob": lambda _a: ("approve", "ok"),
            "carol": lambda _a: ("approve", "ok"),
        }
        profile = ProcessProfile()
        arena = Arena(storage, judges, profile=profile)
        flow = MulticodersFlow(
            storage, dispatcher, arena, research_node=ResearchNode(storage), profile=profile
        )

        result = flow.run("define a vacation request workflow")

        assert result.final_status == "completed"
        assert result.winner is not None
        assert result.qa_report is not None and result.qa_report.passed
        # A Python QA gate would have rejected this JSON; the process profile accepts it.


def test_process_objective_filter_rejects_non_json_candidate():
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(os.path.join(tmp, "mc.db"))
        dispatcher = Dispatcher(storage, {"alice": lambda _p: "this is not json"})
        arena = Arena(storage, {"bob": lambda _a: ("approve", "ok")}, profile=ProcessProfile())
        flow = MulticodersFlow(
            storage, dispatcher, arena, research_node=ResearchNode(storage), profile=ProcessProfile()
        )
        result = flow.run("define a workflow")
        # Filtered out -> no consensus -> needs_human.
        assert result.final_status == "needs_human"
