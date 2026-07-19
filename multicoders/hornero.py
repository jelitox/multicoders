"""Hornero integration for Multicoders.

The adapter keeps Hornero's full command surface available from the
Multicoders entry point without copying or drifting its lifecycle, SDD,
knowledge, assurance, and runtime implementations.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    """Run Hornero's CLI with the current repository as its working context."""
    args = list(argv or [])
    executable = shutil.which("hornero")
    if executable:
        command = [executable, *args]
        return subprocess.call(command)
    try:
        from hornero.cli import main as hornero_main
    except ImportError as exc:
        print(
            "Hornero is not installed. Install with: "
            "python -m pip install 'multicoders[hornero]'",
            file=sys.stderr,
        )
        return 2
    return int(hornero_main(args))


def discover_root(start: Path | None = None) -> Path:
    """Return the nearest repository root for integrations needing a path."""
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current
