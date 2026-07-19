"""Environment configuration and provider validation."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List


PROVIDER_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
}


@dataclass
class ProviderStatus:
    available: List[str]
    missing: List[str]


def detect_providers() -> ProviderStatus:
    available: List[str] = []
    missing: List[str] = []
    for name, env_var in PROVIDER_ENV.items():
        if os.environ.get(env_var):
            available.append(name)
        else:
            missing.append(name)
    return ProviderStatus(available=available, missing=missing)


def require_any_provider() -> ProviderStatus:
    status = detect_providers()
    if not status.available:
        raise RuntimeError(
            "No LLM provider credentials found. Set one of: "
            + ", ".join(PROVIDER_ENV.values())
            + " — or run with --dry-run to use deterministic mocks."
        )
    return status
