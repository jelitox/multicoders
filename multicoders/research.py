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
        import os
        from pathlib import Path
        
        repo_root = Path("/home/jelitox/repos/labs/multicoders")
        files = []
        try:
            for root, dirs, filenames in os.walk(repo_root):
                if ".git" in dirs:
                    dirs.remove(".git")
                if "__pycache__" in dirs:
                    dirs.remove("__pycache__")
                for f in filenames:
                    files.append(os.path.relpath(os.path.join(root, f), repo_root))
                if len(files) > 50:
                    break
        except Exception:
            pass

        file_list = "\n".join(f"- {f}" for f in files[:30])
        
        enriched_prompt = (
            f"Context: Deterministic execution in multicoders project.\n"
            f"Repository Files:\n{file_list}\n"
            f"Task: {prompt}\n"
            f"Constraints: Use standard libraries. Pass ast.parse. Follow project conventions."
        )
        metadata = {"source": "local", "file_count": len(files), "complexity": "medium" if len(files) > 10 else "low"}
        
        return ResearchContext(
            task_id=task_id,
            raw_prompt=prompt,
            enriched_prompt=enriched_prompt,
            metadata=metadata,
        )
