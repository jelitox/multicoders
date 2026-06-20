"""In-process working memory: per-run staging of pipeline phases."""
from __future__ import annotations

from typing import Any


class InProcessWorkingMemory:
    """Namespaced (by ``run_id``) key/value staging held in memory.

    Lets each engine stage a phase's output (research, dispatch, arena, qa)
    under a stable key instead of concatenating ever-growing payloads into the
    next prompt. A run's namespace is dropped with :meth:`clear_run`.
    """

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}

    def stage(self, run_id: str, key: str, value: Any) -> None:
        self._store.setdefault(run_id, {})[key] = value

    def staged(self, run_id: str, key: str | None = None) -> Any:
        bucket = self._store.get(run_id, {})
        if key is None:
            return dict(bucket)
        return bucket.get(key)

    def clear_run(self, run_id: str) -> None:
        self._store.pop(run_id, None)
