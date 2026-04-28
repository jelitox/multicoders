from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from multicoders.app import (
    AgentConfig,
    RepoContext,
    brainstorm_aggregate_proposal_scores,
    choose_fallback_winner,
    parse_brainstorming_command,
    phase_payload_is_valid,
    run_brainstorming_step,
    start_brainstorming_session,
    select_validation_commands,
    TelegramState,
)
from multicoders.stacks import StackProfile
from multicoders.storage import connect_db, create_task, get_task, init_db, retry_task, update_task_status


class ConsensusTests(unittest.TestCase):
    def test_choose_fallback_winner_returns_plurality(self) -> None:
        specs = [
            {"solution_id": "codex-solution"},
            {"solution_id": "claude-solution"},
            {"solution_id": "gemini-solution"},
        ]
        votes = [
            {"vote_for": "codex-solution"},
            {"vote_for": "codex-solution"},
            {"vote_for": "claude-solution"},
        ]
        self.assertEqual(choose_fallback_winner(specs, votes), "codex-solution")

    def test_retry_task_resets_failed_task_to_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            conn = connect_db(Path(temp_dir) / "service.db")
            init_db(conn)
            task_id = create_task(
                conn,
                repo_path="/tmp/repo",
                task_type="bugfix",
                task_text="fix thing",
                lead_provider=None,
                created_at="2026-01-01T00:00:00+00:00",
                requester="tester",
                request_update_id=1,
            )
            update_task_status(
                conn,
                task_id=task_id,
                status="failed",
                updated_at="2026-01-01T00:01:00+00:00",
                approved_at="2026-01-01T00:00:30+00:00",
                run_id="run-1",
                result_json='{"error":"boom"}',
            )
            self.assertTrue(retry_task(conn, task_id=task_id, updated_at="2026-01-01T00:02:00+00:00"))
            task = get_task(conn, task_id)
            assert task is not None
            self.assertEqual(task.status, "pending")
            self.assertIsNone(task.approved_at)
            self.assertIsNone(task.run_id)
            self.assertIsNone(task.result_json)

    def test_phase_payload_is_valid_requires_vote_fields(self) -> None:
        self.assertFalse(phase_payload_is_valid("vote", {"solution_id": "", "vote_for": ""}))
        self.assertTrue(phase_payload_is_valid("vote", {"solution_id": "claude-vote", "vote_for": "codex-solution"}))

    def test_update_task_status_rejects_unknown_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            conn = connect_db(Path(temp_dir) / "service.db")
            init_db(conn)
            task_id = create_task(
                conn,
                repo_path="/tmp/repo",
                task_type="bugfix",
                task_text="fix thing",
                lead_provider=None,
                created_at="2026-01-01T00:00:00+00:00",
                requester="tester",
                request_update_id=1,
            )
            with self.assertRaises(ValueError):
                update_task_status(conn, task_id=task_id, status="mystery", updated_at="2026-01-01T00:01:00+00:00")

    def test_select_validation_commands_uses_unittest_when_pytest_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / "tests").mkdir()
            context = RepoContext(
                repo=repo,
                branch="main",
                recent_commit="none",
                stack=StackProfile(key="python", label="Python", rationale="", rules=[]),
                candidate_files=[],
            )
            commands = select_validation_commands(context)
            if not any(command[:2] == ["pytest", "-q"] for command in commands):
                self.assertIn(["python3", "-m", "unittest", "discover", "-s", "tests"], commands)

    def test_brainstorming_scoring_prefers_peer_supported_proposal(self) -> None:
        proposals = [
            {"proposal_id": "codex-brainstorm-r1-proposal", "author": "codex", "summary": "codex", "self_score": 6},
            {"proposal_id": "claude-brainstorm-r1-proposal", "author": "claude", "summary": "claude", "self_score": 9},
            {"proposal_id": "gemini-brainstorm-r1-proposal", "author": "gemini", "summary": "gemini", "self_score": 7},
        ]
        score_payloads = [
            {"scorer": "codex", "scores": {"codex-brainstorm-r1-proposal": 6, "claude-brainstorm-r1-proposal": 8, "gemini-brainstorm-r1-proposal": 7}},
            {"scorer": "claude", "scores": {"codex-brainstorm-r1-proposal": 7, "claude-brainstorm-r1-proposal": 9, "gemini-brainstorm-r1-proposal": 8}},
            {"scorer": "gemini", "scores": {"codex-brainstorm-r1-proposal": 7, "claude-brainstorm-r1-proposal": 8, "gemini-brainstorm-r1-proposal": 6}},
        ]
        ranking = brainstorm_aggregate_proposal_scores(proposals, score_payloads)
        self.assertEqual(ranking[0]["proposal_id"], "claude-brainstorm-r1-proposal")
        self.assertGreater(ranking[0]["aggregate_score"], ranking[1]["aggregate_score"])

    def test_brainstorming_dry_run_reaches_final_spec(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / "README.md").write_text("sample\n", encoding="utf-8")
            state = TelegramState(state_file=repo / "state.json", brainstorming=None)
            context = RepoContext(
                repo=repo,
                branch="no-git",
                recent_commit="no commits",
                stack=StackProfile(key="generic", label="Generic", rationale="", rules=[]),
                candidate_files=["README.md"],
            )
            agents = [
                AgentConfig(provider="codex", display_name="codex-bot", model=None, bot=None),
                AgentConfig(provider="claude", display_name="claude-bot", model=None, bot=None),
                AgentConfig(provider="gemini", display_name="gemini-bot", model=None, bot=None),
            ]

            start_brainstorming_session(state=state, topic="construir un algoritmo isomorfico", sender_name="Jelitox", repo=repo, dry_run=True)
            for _ in range(8):
                if state.brainstorming is None or not state.brainstorming.get("active"):
                    break
                run_brainstorming_step(
                    state=state,
                    agents=agents,
                    context=context,
                    dry_run=True,
                    timeout_sec=30,
                    observer_bot=None,
                )

            assert state.brainstorming is not None
            self.assertFalse(state.brainstorming["active"])
            self.assertEqual(state.brainstorming["status"], "completed")
            self.assertIn("spec_path", state.brainstorming)
            self.assertIn("isomorfico", state.brainstorming["topic"])

    def test_parse_brainstorming_command_supports_group_syntax(self) -> None:
        self.assertEqual(parse_brainstorming_command("/brainstorming construir un algoritmo isomorfico"), "construir un algoritmo isomorfico")
        self.assertEqual(parse_brainstorming_command("/brainstorming@multicoders construir un algoritmo isomorfico"), "construir un algoritmo isomorfico")


if __name__ == "__main__":
    unittest.main()
