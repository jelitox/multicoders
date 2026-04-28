from __future__ import annotations

import dataclasses
from pathlib import Path


@dataclasses.dataclass(slots=True)
class StackProfile:
    key: str
    label: str
    rationale: str
    rules: list[str]


def _exists(repo: Path, relative_path: str) -> bool:
    return (repo / relative_path).exists()


def detect_stack(repo: Path) -> StackProfile:
    if any(
        _exists(repo, path)
        for path in ("pyproject.toml", "setup.py", "requirements.txt", "uv.lock", "poetry.lock", "Pipfile")
    ):
        return StackProfile(
            key="python",
            label="Python",
            rationale="Detected Python packaging, dependency, or lock files.",
            rules=[
                "Prefer focused fixes with tests when practical.",
                "Avoid broad formatting churn or unrelated refactors.",
                "If a feature is requested, define acceptance criteria before editing code.",
                "Prefer python -m unittest or pytest if tests are present.",
            ],
        )
    if any(
        _exists(repo, path)
        for path in ("package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb")
    ):
        return StackProfile(
            key="javascript",
            label="JavaScript/TypeScript",
            rationale="Detected Node, frontend, or package-manager files.",
            rules=[
                "Prefer small bug fixes, tests, and concrete UX improvements.",
                "Preserve the existing visual language and project conventions.",
                "Do not touch lockfiles unless the change strictly requires it.",
            ],
        )
    if _exists(repo, "go.mod"):
        return StackProfile(
            key="go",
            label="Go",
            rationale="Detected Go module metadata.",
            rules=[
                "Prefer small correctness or test fixes within current package boundaries.",
                "Keep the change narrow and easy to validate locally.",
            ],
        )
    if _exists(repo, "Cargo.toml"):
        return StackProfile(
            key="rust",
            label="Rust",
            rationale="Detected Rust cargo manifest.",
            rules=[
                "Prefer small fixes with clear correctness or test value.",
                "Keep ownership or lifetime changes minimal unless required.",
            ],
        )
    if _exists(repo, "Dockerfile") or _exists(repo, "compose.yaml") or _exists(repo, "docker-compose.yml"):
        return StackProfile(
            key="devops",
            label="DevOps/Containers",
            rationale="Detected container or orchestration configuration.",
            rules=[
                "Prefer safe reliability improvements, docs, or config fixes.",
                "Do not alter deployment semantics unless the benefit is small and obvious.",
            ],
        )
    return StackProfile(
        key="generic",
        label="Generic",
        rationale="No stronger stack match found.",
        rules=[
            "Prefer high-confidence changes backed by local evidence.",
            "For features, align on a concise spec before implementation.",
        ],
    )
