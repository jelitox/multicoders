"""Research Node: Context gathering before dispatching.

This node is responsible for taking a raw prompt and gathering context
to form a structured payload for the Dispatcher.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from .storage import Storage


@dataclass
class ResearchContext:
    task_id: str
    raw_prompt: str
    enriched_prompt: str
    metadata: Dict[str, Any]


class ResearchNode:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    def enrich(self, task_id: str, prompt: str) -> ResearchContext:
        # In the future, this will call Parrot's search tools, read codebase,
        # or parse Jira tickets. For now, it's a structural pass-through.
        enriched_prompt = (
            f"Context: Ensure deterministic execution.\n"
            f"Task: {prompt}\n"
            f"Constraints: Use standard libraries where possible. Pass ast.parse."
        )
        metadata = {"source": "local", "complexity": "unknown"}
        
        # We don't create the task here, as the Flow already does it.
        # We just return the enriched data.
        return ResearchContext(
            task_id=task_id,
            raw_prompt=prompt,
            enriched_prompt=enriched_prompt,
            metadata=metadata,
        )
