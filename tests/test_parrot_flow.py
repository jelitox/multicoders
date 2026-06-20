"""Tests for ParrotMulticodersFlow."""
import unittest
from unittest.mock import ANY, AsyncMock, MagicMock

from multicoders.parrot_flow import ParrotMulticodersFlow
from multicoders.storage import Storage
from multicoders.dispatcher import Dispatcher, Candidate
from multicoders.arena import Arena, ArenaVerdict
from multicoders.research import ResearchNode, ResearchContext
from multicoders.qa import QANode, QAReport


class TestParrotFlow(unittest.TestCase):
    def setUp(self):
        self.storage = MagicMock(spec=Storage)
        self.dispatcher = MagicMock(spec=Dispatcher)
        self.arena = MagicMock(spec=Arena)
        self.research = MagicMock(spec=ResearchNode)
        self.qa = MagicMock(spec=QANode)

        self.flow = ParrotMulticodersFlow(
            self.storage, self.dispatcher, self.arena, self.research, self.qa
        )
        # The commit step talks to a real GitIntegration; stub it so the test
        # never touches the working tree.
        self.flow.git = MagicMock()
        self.flow.git.commit_winner = AsyncMock(return_value=None)

    def test_flow_success(self):
        self.research.enrich.return_value = ResearchContext(
            task_id="t1", raw_prompt="hi", enriched_prompt="enriched hi", metadata={}
        )
        candidate = Candidate(artifact_id=1, author="codex", content="print(1)")
        # Mock returns a plain list (run() awaits via _maybe_await only if awaitable).
        self.dispatcher.dispatch.return_value = [candidate]
        verdict = ArenaVerdict(artifact_id=1, status="approved", approvals=2, rejections=0)
        self.arena.run.return_value = [verdict]
        self.qa.check.return_value = QAReport(artifact_id=1, passed=True, reason="ok")

        result = self.flow.run("hi")

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["winner"], "codex")
        # task_id is a generated uuid — assert the status arg, not the exact id.
        self.storage.update_task_status.assert_called_with(ANY, "completed")
        self.flow.git.commit_winner.assert_awaited_once()

    def test_flow_fails_when_no_candidate_passes_qa(self):
        self.research.enrich.return_value = ResearchContext(
            task_id="t1", raw_prompt="hi", enriched_prompt="enriched hi", metadata={}
        )
        candidate = Candidate(artifact_id=1, author="codex", content="print(1)")
        self.dispatcher.dispatch.return_value = [candidate]
        # Arena rejects the only candidate -> QA never yields a winner.
        verdict = ArenaVerdict(artifact_id=1, status="rejected", approvals=0, rejections=2)
        self.arena.run.return_value = [verdict]

        result = self.flow.run("hi")

        self.assertEqual(result["status"], "failed")
        self.storage.update_task_status.assert_called_with(ANY, "failed")
        self.flow.git.commit_winner.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
