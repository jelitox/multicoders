"""Domain profiles that make the orchestrator domain-pluggable (SPEC §3.3)."""
from __future__ import annotations

from .base import DomainProfile, ValidationOutcome
from .code import CodeProfile
from .process import ProcessProfile

_REGISTRY: dict[str, type] = {
    CodeProfile.name: CodeProfile,
    ProcessProfile.name: ProcessProfile,
}


def get_profile(name: str) -> DomainProfile:
    """Instantiate a registered domain profile by name."""
    try:
        return _REGISTRY[name]()
    except KeyError as exc:
        raise ValueError(
            f"unknown domain profile {name!r}; known: {', '.join(sorted(_REGISTRY))}"
        ) from exc


def list_profiles() -> list[str]:
    return sorted(_REGISTRY)


__all__ = [
    "DomainProfile",
    "ValidationOutcome",
    "CodeProfile",
    "ProcessProfile",
    "get_profile",
    "list_profiles",
]
