"""Factory and utility methods for the Multicoders project.

This module provides a simple entry point to instantiate the entire stack
with all Parrot dependencies correctly configured.
"""
from __future__ import annotations

import os
from typing import Optional

from .arena import Arena
from .dispatcher import Dispatcher
from .parrot_flow import ParrotMulticodersFlow
from .qa import QANode
from .research import ResearchNode
from .storage import Storage


def create_multicoders_flow(
    storage: Optional[Storage] = None,
    db_path: str = "multicoders.db",
    workdir_root: str = "/tmp/multicoders-runs",
) -> ParrotMulticodersFlow:
    """Creates a fully configured ParrotMulticodersFlow instance."""
    from .dispatcher import ParrotCoder

    # Ensure workdir exists
    if not os.path.exists(workdir_root):
        os.makedirs(workdir_root, exist_ok=True)

    storage = storage or Storage(db_path)

    # Initialize coders using Parrot agents
    coders = [
        ParrotCoder(name="Codex", role="Python Senior Developer"),
        ParrotCoder(name="Claude", role="Security Specialist & Architect"),
        ParrotCoder(name="Gemini", role="Efficiency & Performance Guru")
    ]

    dispatcher = Dispatcher(storage, coders=coders, worktree_root=workdir_root)
    arena = Arena(storage)
    research_node = ResearchNode(storage)
    qa_node = QANode(storage)

    return ParrotMulticodersFlow(
        storage=storage,
        dispatcher=dispatcher,
        arena=arena,
        research_node=research_node,
        qa_node=qa_node
    )


def create_multicoders_stack(
    db_path: str = "multicoders.db",
    workdir_root: str = "/tmp/multicoders-runs",
) -> ParrotMulticodersFlow:
    """Backward-compatible factory name used by older entry points/tests."""
    return create_multicoders_flow(db_path=db_path, workdir_root=workdir_root)


def quick_run(prompt: str, db_path: str = "multicoders.db") -> dict:
    """Helper for a single-line execution of the Multicoders flow."""
    flow = create_multicoders_flow(db_path=db_path)
    return flow.run(prompt)
