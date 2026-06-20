"""Episodic/semantic memory backed by Parrot GraphIndex (Fase 4b).

Feature-detected like :mod:`multicoders.memory.pageindex`: the pinned ai-parrot
lacks ``parrot.knowledge.graphindex`` (it targets FEAT-190/191). Until the
submodule is bumped, this layer transparently delegates recall to a fallback
:class:`DecisionMemoryLayer` (the Fase 4a JSON store), so behaviour degrades to
keyword recall instead of breaking.

When GraphIndex is present, decisions become ``UniversalNode``s and recall uses
``search_hybrid`` / ``relevance`` over the knowledge graph (communities =
recurring themes, centrality = key patterns).
"""
from __future__ import annotations

from typing import Any

from .base import Decision, Recall


def graphindex_available() -> bool:
    """True if the installed ai-parrot exposes the GraphIndex toolkit."""
    try:  # pragma: no cover - depends on the pinned ai-parrot version
        from parrot_tools.graphindex import GraphIndexToolkit  # noqa: F401

        return True
    except Exception:
        return False


class GraphIndexEpisodicMemory:
    """Decision memory that prefers GraphIndex recall, falling back gracefully.

    Parameters
    ----------
    fallback:
        A :class:`DecisionMemoryLayer` used both as the durable record sink and
        as the recall path while GraphIndex is unavailable.
    toolkit:
        A ready ``GraphIndexToolkit`` (or compatible). When ``None`` the layer
        delegates entirely to ``fallback``.
    """

    def __init__(self, fallback: Any, *, toolkit: Any = None) -> None:
        self._fallback = fallback
        self._toolkit = toolkit

    def available(self) -> bool:
        return self._toolkit is not None

    def record(self, decision: Decision) -> None:
        # Always persist to the durable fallback; mirror into the graph when present.
        self._fallback.record(decision)
        if self._toolkit is not None:  # pragma: no cover - needs GraphIndex
            self._ingest_into_graph(decision)

    def recall(self, query: str, *, limit: int = 5) -> list[Recall]:
        if self._toolkit is None:
            return self._fallback.recall(query, limit=limit)
        return self._recall_from_graph(query, limit=limit)  # pragma: no cover

    # ---- GraphIndex-backed paths (active only after the bump) ------------
    def _ingest_into_graph(self, decision: Decision) -> None:  # pragma: no cover
        import asyncio

        from parrot.knowledge.graphindex import NodeKind, UniversalNode  # type: ignore

        node = UniversalNode(
            node_id=f"decision-{decision.task_id}",
            kind=NodeKind.CONCEPT,
            title=decision.prompt[:80],
            source_uri=f"decision://{decision.task_id}",
            summary=f"{decision.prompt} -> winner={decision.winner} ({decision.status})",
        )
        asyncio.run(self._toolkit.add_nodes([node]))

    def _recall_from_graph(self, query: str, limit: int) -> list[Recall]:  # pragma: no cover
        import asyncio

        rows = asyncio.run(self._toolkit.search_hybrid(query, top_k=limit))
        recalls: list[Recall] = []
        for row in rows:
            tid = str(row.get("node_id", "")).replace("decision-", "")
            recalls.append(
                Recall(
                    score=float(row.get("combined_score", 0.0)),
                    decision=Decision(
                        task_id=tid,
                        prompt=row.get("title", ""),
                        winner="",
                        status="",
                    ),
                )
            )
        return recalls
