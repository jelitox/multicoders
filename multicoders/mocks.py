"""Deterministic mock coders/judges for --dry-run mode.

Avoids any LLM call — useful for CI, smoke tests, and validating the DAG
without burning tokens.
"""
from __future__ import annotations

from .dispatcher import ParrotCoder
from .arena import ParrotJudge


class MockCoder(ParrotCoder):
    def __init__(self, name: str, snippet: str):
        self.name = name
        self._snippet = snippet
        self.agent = None  # no parrot agent

    async def generate(self, prompt: str) -> str:
        return self._snippet


class MockJudge(ParrotJudge):
    def __init__(self, name: str, vote: str = "approve"):
        self.name = name
        self._vote = vote
        self.agent = None

    async def judge(self, artifact_content: str):
        return self._vote, f"mock {self._vote} from {self.name}"


def default_mock_coders():
    return [
        MockCoder(
            "Claude",
            "def hello():\n    return 'hello from claude mock'\n",
        ),
        MockCoder(
            "Gemini",
            "def hello():\n    return 'hello from gemini mock'\n",
        ),
    ]


def default_mock_judges():
    return [
        MockJudge("Claude"),
        MockJudge("Gemini"),
        MockJudge("Codex"),
    ]
