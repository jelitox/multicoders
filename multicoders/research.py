"""Research Node: Context gathering before dispatching.

This node is responsible for taking a raw prompt and gathering context
to form a structured payload for the Dispatcher.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from .storage import Storage


from .parrot_lens import ParrotLens


@dataclass
class ResearchContext:
    task_id: str
    raw_prompt: str
    enriched_prompt: str
    metadata: Dict[str, Any]


class ResearchNode:
    def __init__(
        self,
        storage: Storage,
        repo_root: Optional[Path] = None,
        memory: Any = None,
    ) -> None:
        self.storage = storage
        self.repo_root = repo_root or Path(__file__).resolve().parents[1]
        self.lens = ParrotLens()
        self.memory = memory

    def enrich(self, task_id: str, prompt: str) -> ResearchContext:
        import os

        repo_root = self.repo_root
        files = []
        parrot_context = ""

        # Documental grounding (PageIndex) supersedes the os.walk file dump when
        # the memory layer is available (Fase 4b); otherwise we fall back below.
        grounding = ""
        if self.memory is not None:
            try:
                grounding = self.memory.ground(prompt) or ""
            except Exception:
                grounding = ""

        parrot_path = repo_root / "_refs/ai-parrot/packages/ai-parrot/src/parrot"
        if not parrot_path.exists():
            # Fallback: Check if ai-parrot is installed in the environment
            try:
                import parrot as parrot_pkg
                parrot_path = Path(parrot_pkg.__file__).resolve().parent
            except ImportError:
                pass

        if parrot_path.exists():
            self.lens.parrot_path = parrot_path
            signatures = self.lens.get_api_surface()
            if signatures:
                parrot_context = "Parrot API surface (real signatures extracted via AST):\n" + signatures + "\n"

        try:
            for root, dirs, filenames in os.walk(repo_root):
                if ".git" in dirs:
                    dirs.remove(".git")
                if "__pycache__" in dirs:
                    dirs.remove("__pycache__")
                if "_refs" in dirs:
                    dirs.remove("_refs")
                for f in filenames:
                    files.append(os.path.relpath(os.path.join(root, f), repo_root))
                if len(files) > 50:
                    break
        except Exception:
            pass

        file_list = "\n".join(f"- {f}" for f in files[:30])

        if grounding:
            repo_section = f"Grounded context (PageIndex retrieval):\n{grounding}\n"
        else:
            repo_section = f"Repository Files:\n{file_list}\n"

        enriched_prompt = (
            f"Context: Deterministic execution in multicoders project.\n"
            f"{repo_section}\n"
            f"{parrot_context}\n"
            f"Task: {prompt}\n"
            f"Constraints: Use standard libraries. Pass ast.parse. Follow project conventions."
        )
        metadata = {
            "source": "pageindex" if grounding else "local",
            "file_count": len(files),
            "grounded": bool(grounding),
            "complexity": "medium" if len(files) > 10 else "low",
        }
        
        return ResearchContext(
            task_id=task_id,
            raw_prompt=prompt,
            enriched_prompt=enriched_prompt,
            metadata=metadata,
        )
