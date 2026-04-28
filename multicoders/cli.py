"""Multicoders CLI: Real Parrot integration.
"""
from __future__ import annotations

import argparse
import sys

from .arena import Arena, ParrotJudge
from .dispatcher import Dispatcher, ParrotCoder
from .flow import MulticodersFlow
from .research import ResearchNode
from .storage import Storage


def main() -> int:
    parser = argparse.ArgumentParser(description="Multicoders Real MVP CLI")
    parser.add_argument("prompt", help="The task prompt")
    parser.add_argument("--db", default="multicoders.db", help="Path to SQLite DB")
    args = parser.parse_args()

    storage = Storage(args.db)
    
    # Setup Real Parrot Coders
    coders = [
        ParrotCoder("Claude", "Python Backend Developer"),
        ParrotCoder("Gemini", "Software Architect"),
    ]
    dispatcher = Dispatcher(storage, coders)
    
    # Setup Real Parrot Judges
    judges = [
        ParrotJudge("Claude"),
        ParrotJudge("Gemini"),
        ParrotJudge("Codex"),
    ]
    arena = Arena(storage, judges)
    
    research = ResearchNode(storage)
    flow = MulticodersFlow(storage, dispatcher, arena, research_node=research)

    print(f"[*] Running real Parrot flow for prompt: {args.prompt!r}")
    try:
        result = flow.run(args.prompt)
    except Exception as e:
        print(f"[!] Flow failed: {e}")
        return 1
    
    print(f"[*] Task ID: {result.task_id}")
    print(f"[*] Status: {result.final_status}")
    
    if result.winner:
        print(f"[*] Winner: {result.winner.author}")
        print("-" * 40)
        print(result.winner.content)
        print("-" * 40)
    else:
        print("[!] No winner found or consensus not reached.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
