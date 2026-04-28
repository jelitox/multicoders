"""Multicoders CLI: Simple entry point for the MVP.
"""
from __future__ import annotations

import argparse
import sys
from typing import Dict

from .arena import Arena
from .dispatcher import Dispatcher
from .flow import MulticodersFlow
from .research import ResearchNode
from .storage import Storage


def mock_coder_claude(prompt: str) -> str:
    return f"def hello():\n    # Claude's solution for: {prompt[:20]}...\n    print('Hello from Claude')"


def mock_coder_gemini(prompt: str) -> str:
    return f"def hello():\n    # Gemini's solution for: {prompt[:20]}...\n    print('Hello from Gemini')"


def mock_judge(artifact: Dict) -> tuple[str, str]:
    return "approve", "Looks good to me"


def main() -> int:
    parser = argparse.ArgumentParser(description="Multicoders MVP CLI")
    parser.add_argument("prompt", help="The task prompt")
    args = parser.parse_args()

    storage = Storage("multicoders.db")
    
    # Setup Mocks for MVP
    coders = {
        "claude": mock_coder_claude,
        "gemini": mock_coder_gemini,
    }
    dispatcher = Dispatcher(storage, coders)
    
    judges = {
        "gemini": mock_judge,
        "claude": mock_judge,
        "codex": mock_judge,
    }
    arena = Arena(storage, judges)
    
    research = ResearchNode(storage)
    flow = MulticodersFlow(storage, dispatcher, arena, research_node=research)

    print(f"[*] Running flow for prompt: {args.prompt!r}")
    result = flow.run(args.prompt)
    
    print(f"[*] Task ID: {result.task_id}")
    print(f"[*] Status: {result.final_status}")
    if result.winner:
        print(f"[*] Winner: {result.winner.author}")
        print("-" * 40)
        print(result.winner.content)
        print("-" * 40)
    else:
        print("[!] No winner found or task failed.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
