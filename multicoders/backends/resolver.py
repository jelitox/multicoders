"""Configurable backend resolution.

Resolution honours an explicit preference order and a capability requirement.
It NEVER auto-inserts a billed backend: if you want API billing you must put an
:class:`~multicoders.backends.api.ApiBackend` in the candidate list yourself.
"""
from __future__ import annotations

from .base import AgentBackend, BackendUnavailable

# Backend class names considered "billed" — kept out of any silent fallback.
_BILLED_BACKENDS = {"ApiBackend"}


def order_backends(
    backends: list[AgentBackend], preference: list[str] | None
) -> list[AgentBackend]:
    """Sort ``backends`` by a preference list of names (unknown names keep order)."""
    if not preference:
        return list(backends)
    rank = {name: i for i, name in enumerate(preference)}
    return sorted(backends, key=lambda b: rank.get(b.name, len(rank)))


async def available_backends(backends: list[AgentBackend]) -> list[AgentBackend]:
    """Filter to backends whose ``health()`` is true, preserving order."""
    healthy: list[AgentBackend] = []
    for backend in backends:
        try:
            if await backend.health():
                healthy.append(backend)
        except Exception:  # noqa: BLE001 - a failing probe just drops the backend
            continue
    return healthy


async def select_backends(
    backends: list[AgentBackend],
    *,
    capability: str | None = None,
    preference: list[str] | None = None,
    minimum: int = 1,
) -> list[AgentBackend]:
    """Return healthy backends matching ``capability``, ordered by preference.

    Raises :class:`BackendUnavailable` if fewer than ``minimum`` qualify. Does
    not add any billed backend on its own.
    """
    ordered = order_backends(backends, preference)
    if capability is not None:
        ordered = [b for b in ordered if capability in b.capabilities()]
    healthy = await available_backends(ordered)
    if len(healthy) < minimum:
        raise BackendUnavailable(
            f"need {minimum} backend(s) with capability {capability!r}, "
            f"found {len(healthy)} healthy"
        )
    return healthy


def is_billed(backend: AgentBackend) -> bool:
    return type(backend).__name__ in _BILLED_BACKENDS
