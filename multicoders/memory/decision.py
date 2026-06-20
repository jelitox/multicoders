"""Cross-run decision memory backed by a JSON-lines file.

This is the Fase 4a baseline for "did we solve something like this before?":
durable, dependency-free, and offline. Recall uses token-overlap scoring over
prompts + tags. Fase 4b upgrades this to GraphIndex (semantic/episodic) while
keeping the same :class:`DecisionMemoryLayer` contract.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .base import Decision, Recall

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall((text or "").lower()))


class JsonDecisionMemory:
    """Append-only JSON-lines decision store with keyword recall."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def record(self, decision: Decision) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(decision.__dict__, ensure_ascii=False) + "\n")

    def _load(self) -> list[Decision]:
        if not self.path.exists():
            return []
        decisions: list[Decision] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                decisions.append(Decision(**json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue
        return decisions

    def recall(self, query: str, *, limit: int = 5) -> list[Recall]:
        q = _tokens(query)
        if not q:
            return []
        hits: list[Recall] = []
        for decision in self._load():
            corpus = _tokens(decision.prompt) | {t.lower() for t in decision.tags}
            if not corpus:
                continue
            overlap = len(q & corpus)
            if overlap == 0:
                continue
            score = overlap / len(q | corpus)  # Jaccard
            hits.append(Recall(score=round(score, 4), decision=decision))
        hits.sort(key=lambda r: r.score, reverse=True)
        return hits[:limit]
