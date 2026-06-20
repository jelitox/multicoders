"""Multicoders CLI: Real Parrot integration with --dry-run fallback."""
from __future__ import annotations

import argparse
import sys

from .arena import Arena, ParrotJudge
from .config import require_any_provider
from .dispatcher import Dispatcher, ParrotCoder
from .flow import MulticodersFlow
from .mocks import default_mock_coders, default_mock_judges
from .research import ResearchNode
from .storage import Storage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="multicoders arena",
        description="Multicoders deterministic Parrot arena engine",
    )
    parser.add_argument("prompt", nargs="?", help="The task prompt")
    parser.add_argument("--db", default="multicoders.db", help="Path to SQLite DB")
    parser.add_argument("--resume", help="Task ID to resume from the last successful checkpoint.")
    parser.add_argument(
        "--force-research",
        action="store_true",
        help="When resuming, invalidate research and start over.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use deterministic mock coders/judges (no LLM calls).",
    )
    parser.add_argument(
        "--worktree",
        help="Path to the root directory for candidate worktrees (isolation).",
    )
    args = parser.parse_args(argv)
    if not args.resume and not args.prompt:
        parser.error("prompt is required unless --resume is used")

    storage = Storage(args.db)

    if args.dry_run:
        print("[*] Dry-run: using deterministic mocks (no LLM calls).")
        coders = default_mock_coders()
        judges = default_mock_judges()
    else:
        try:
            status = require_any_provider()
        except RuntimeError as exc:
            print(f"[!] {exc}")
            return 2
        print(f"[*] Providers available: {', '.join(status.available)}")
        coders = [
            ParrotCoder("Claude", "Python Backend Developer"),
            ParrotCoder("Gemini", "Software Architect"),
        ]
        judges = [
            ParrotJudge("Claude"),
            ParrotJudge("Gemini"),
            ParrotJudge("Codex"),
        ]

    dispatcher = Dispatcher(storage, coders, worktree_root=args.worktree)
    arena = Arena(storage, judges)
    research = ResearchNode(storage)
    flow = MulticodersFlow(storage, dispatcher, arena, research_node=research)

    if args.resume:
        print(f"[*] Resuming task: {args.resume!r}")
        try:
            result = flow.resume(args.resume, force_research=args.force_research)
        except Exception as exc:
            print(f"[!] Resume failed: {exc}")
            return 1
    else:
        prompt = args.prompt or ""
        print(f"[*] Running flow for prompt: {prompt!r}")
        try:
            result = flow.run(prompt)
        except Exception as exc:
            print(f"[!] Flow failed: {exc}")
            return 1

    print(f"[*] Task ID: {result.task_id}")
    print(f"[*] Status: {result.final_status}")

    if result.winner:
        print(f"[*] Winner: {result.winner.author}")
        if result.qa_report:
            print(f"[*] QA: {result.qa_report.reason}")
        print("-" * 40)
        print(result.winner.content)
        print("-" * 40)
    else:
        print("[!] No winner found or consensus not reached.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
