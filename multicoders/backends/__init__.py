"""Provider-agnostic agent backends (SPEC §3.1).

A single :class:`AgentBackend` protocol consumed by both engines, with variants
for in-process Parrot, official provider CLIs (BYO-auth), provider API keys, and
local models, plus deterministic mocks for dry-run/tests.
"""
from __future__ import annotations

from .adapter import BackendCoder, BackendJudge
from .api import ApiBackend, LocalBackend
from .base import (
    AgentBackend,
    AgentContext,
    Artifact,
    BackendError,
    BackendUnavailable,
    TaskSpec,
)
from .cli import CliBackend
from .mock import MockBackend, default_mock_backends
from .parrot import ParrotBackend
from .resolver import (
    available_backends,
    is_billed,
    order_backends,
    select_backends,
)

__all__ = [
    "AgentBackend",
    "AgentContext",
    "Artifact",
    "BackendError",
    "BackendUnavailable",
    "TaskSpec",
    "BackendCoder",
    "BackendJudge",
    "MockBackend",
    "default_mock_backends",
    "ParrotBackend",
    "CliBackend",
    "ApiBackend",
    "LocalBackend",
    "available_backends",
    "order_backends",
    "select_backends",
    "is_billed",
]
