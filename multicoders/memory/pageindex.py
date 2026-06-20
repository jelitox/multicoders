"""Documental/code memory backed by Parrot PageIndex (Fase 4b).

Feature-detected: the pinned ``ai-parrot`` (commit 271aba90) does NOT ship
``parrot.knowledge.pageindex`` — it arrives in a newer version (FEAT-198). This
layer therefore probes for the module at construction time and reports
``available() == False`` when it is missing, so the rest of Multicoders keeps
working unchanged. Once the submodule is bumped, the same layer activates
automatically and grounds research via ``retrieve()`` instead of an ``os.walk``
file-list dump.

The module import path is the post-FEAT-198 one:
``from parrot.knowledge.pageindex import PageIndexToolkit``.
"""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any


def pageindex_available() -> bool:
    """True if the installed ai-parrot exposes the PageIndex toolkit."""
    try:  # pragma: no cover - depends on the pinned ai-parrot version
        from parrot.knowledge.pageindex import PageIndexToolkit  # noqa: F401

        return True
    except Exception:
        return False


def _run(awaitable: Any) -> Any:
    if not inspect.isawaitable(awaitable):
        return awaitable
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    import nest_asyncio

    nest_asyncio.apply()
    return asyncio.get_event_loop().run_until_complete(awaitable)


class PageIndexDocumentMemory:
    """Ground queries against a PageIndex tree built from the repo/docs.

    Parameters
    ----------
    toolkit:
        A ready ``PageIndexToolkit`` (or compatible object exposing async
        ``retrieve(tree_name, query, top_k)``). When ``None``, the layer is
        inert and :meth:`available` is ``False``.
    tree_name:
        Name of the PageIndex tree to retrieve from.
    """

    def __init__(self, toolkit: Any = None, *, tree_name: str = "repo") -> None:
        self._toolkit = toolkit
        self.tree_name = tree_name

    def available(self) -> bool:
        return self._toolkit is not None

    def ground(self, query: str, *, top_k: int = 5) -> str:
        if self._toolkit is None:
            return ""
        result = _run(self._toolkit.retrieve(self.tree_name, query, top_k=top_k))
        return result if isinstance(result, str) else str(result or "")

    # ---- builder ---------------------------------------------------------
    @classmethod
    def build_from_repo(
        cls,
        repo: str | Path,
        *,
        storage_dir: str | Path,
        tree_name: str = "repo",
        client: Any = None,
        light_model: str | None = None,
    ) -> "PageIndexDocumentMemory":
        """Construct a PageIndex tree from a repo's markdown/text files.

        Returns an inert layer (``available() is False``) when PageIndex is not
        installed, so callers never need to special-case the missing dependency.
        """
        if not pageindex_available():
            return cls(None, tree_name=tree_name)

        from parrot.knowledge.pageindex import (  # type: ignore
            PageIndexLLMAdapter,
            PageIndexToolkit,
        )

        adapter = PageIndexLLMAdapter(client=client, model=light_model)
        toolkit = PageIndexToolkit(adapter=adapter, storage_dir=Path(storage_dir))
        existing = _run(toolkit.list_trees())
        if tree_name not in existing:
            _run(toolkit.create_tree(tree_name, doc_name=str(repo)))
            for path in sorted(Path(repo).rglob("*.md")):
                _run(toolkit.import_file(tree_name, str(path)))
        return cls(toolkit, tree_name=tree_name)
