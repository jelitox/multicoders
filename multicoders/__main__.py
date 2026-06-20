"""Multicoders unified command-line entry point.

Multicoders ships two execution engines that share this package:

* The council / service engine (``app.py``): runs Codex, Claude and Gemini
  CLIs over a real repository, mirrors the discussion through Telegram and
  converges on a 2-of-3 solution. Subcommands: ``run``, ``service``,
  ``brainstorming``, ``send-test-messages``, ``discover-telegram-chat``.

* The deterministic Parrot arena engine (``cli.py``): runs the in-process
  Research -> Dispatch -> Arena -> QA flow against Parrot ``BasicAgent``
  instances (or deterministic mocks under ``--dry-run``). Subcommand:
  ``arena``.

This module is the single dispatcher both engines hang off, so that the
systemd unit's ``python -m multicoders service ...`` and the documented
``python -m multicoders arena --dry-run ...`` both resolve correctly.
"""
from __future__ import annotations

import sys

# Subcommands routed to the deterministic Parrot arena engine (cli.py).
ARENA_COMMANDS = {"arena"}


def _print_top_help() -> None:
    print("multicoders — unified multi-agent orchestration")
    print()
    print("Council / service engine (Codex + Claude + Gemini CLIs):")
    print("  multicoders run --repo PATH --task '...'")
    print("  multicoders service --db-file ... --telegram-state-file ...")
    print("  multicoders brainstorming --repo PATH --topic '...'")
    print("  multicoders send-test-messages | discover-telegram-chat")
    print()
    print("Deterministic Parrot arena engine (in-process):")
    print("  multicoders arena [--dry-run] 'prompt'")
    print("  multicoders arena --resume TASK_ID")
    print()
    print("Per-engine help:")
    print("  multicoders run --help")
    print("  multicoders arena --help")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv or argv[0] in {"-h", "--help"}:
        _print_top_help()
        return 0

    if argv[0] in ARENA_COMMANDS:
        from .cli import main as arena_main

        return arena_main(argv[1:])

    from .app import main as council_main

    return council_main(argv)


if __name__ == "__main__":
    sys.exit(main())
