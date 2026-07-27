"""Independent, two-phase provider perspectives.

Phase A gives every provider the same neutral brief concurrently. Phase B
compares immutable first-pass outputs only after every slot has closed.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from multicoders.providers import (
    ProviderError,
    ProviderQuotaError,
    ProviderResult,
    extract_json_object,
    run_provider,
)

ADVISORY_PROTOCOL_VERSION = 1
ADVISORY_MODES = {"review", "brainstorm", "research", "design-opinion"}
DISPOSITIONS = {"CONFIRM", "REJECT", "ESCALATE"}
_SPACE = re.compile(r"\s+")
_SECRET = re.compile(
    r"(?i)((?:api[_-]?key|token|secret|password)\s*[:=]\s*)[^\s,;}]+"
)


class AdvisoryError(ValueError):
    """The advisory request or result violates the protocol."""


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    locator: str
    sha256: str
    content: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "EvidenceItem":
        return cls(
            locator=_required_text(raw, "locator"),
            sha256=_required_text(raw, "sha256"),
            content=_required_text(raw, "content"),
        )


@dataclass(frozen=True, slots=True)
class ConstraintItem:
    source: str
    quote: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ConstraintItem":
        return cls(
            source=_required_text(raw, "source"),
            quote=_required_text(raw, "quote"),
        )


@dataclass(frozen=True, slots=True)
class ReviewTarget:
    kind: str = "context"
    value: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "ReviewTarget":
        payload = raw or {}
        kind = str(payload.get("kind") or "context")
        if kind not in {"context", "uncommitted", "base", "commit"}:
            raise AdvisoryError(f"unsupported review target: {kind}")
        return cls(kind=kind, value=str(payload.get("value") or ""))


@dataclass(frozen=True, slots=True)
class AdvisoryRequest:
    mode: str
    requirement: str
    question: str
    providers: tuple[str, ...]
    evidence: tuple[EvidenceItem, ...] = ()
    constraints: tuple[ConstraintItem, ...] = ()
    review_target: ReviewTarget = field(default_factory=ReviewTarget)
    minimum_quorum: int = 2
    required_providers: tuple[str, ...] = ()
    models: dict[str, str] = field(default_factory=dict)
    timeout_sec: int = 600
    autonomous: bool = False
    resume: bool = False
    protocol_version: int = ADVISORY_PROTOCOL_VERSION

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AdvisoryRequest":
        providers = tuple(_string_list(raw.get("providers"), "providers"))
        request = cls(
            mode=_required_text(raw, "mode"),
            requirement=_required_text(raw, "requirement"),
            question=_required_text(raw, "question"),
            providers=providers,
            evidence=tuple(
                EvidenceItem.from_dict(_mapping(item, "evidence item"))
                for item in _list(raw.get("evidence", []), "evidence")
            ),
            constraints=tuple(
                ConstraintItem.from_dict(_mapping(item, "constraint item"))
                for item in _list(raw.get("constraints", []), "constraints")
            ),
            review_target=ReviewTarget.from_dict(
                _mapping(raw["review_target"], "review_target")
                if raw.get("review_target") is not None
                else None
            ),
            minimum_quorum=_positive_int(raw.get("minimum_quorum", 2), "minimum_quorum"),
            required_providers=tuple(
                _string_list(raw.get("required_providers", []), "required_providers")
            ),
            models={
                str(key): str(value)
                for key, value in _mapping(raw.get("models", {}), "models").items()
            },
            timeout_sec=_positive_int(raw.get("timeout_sec", 600), "timeout_sec"),
            autonomous=bool(raw.get("autonomous", False)),
            resume=bool(raw.get("resume", False)),
            protocol_version=int(
                raw.get("protocol_version", ADVISORY_PROTOCOL_VERSION)
            ),
        )
        request.validate()
        return request

    def validate(self) -> None:
        if self.protocol_version != ADVISORY_PROTOCOL_VERSION:
            raise AdvisoryError(
                f"unsupported advisory protocol version: {self.protocol_version}"
            )
        if self.mode not in ADVISORY_MODES:
            raise AdvisoryError(f"unsupported advisory mode: {self.mode}")
        if len(self.providers) != len(set(self.providers)):
            raise AdvisoryError("providers must not contain duplicates")
        if self.minimum_quorum > len(self.providers):
            raise AdvisoryError("minimum_quorum exceeds provider count")
        if not set(self.required_providers).issubset(self.providers):
            raise AdvisoryError("required_providers must be selected providers")
        if self.resume and self.providers != ("codex",):
            raise AdvisoryError("resume currently requires codex as the sole provider")
        for evidence in self.evidence:
            actual = hashlib.sha256(evidence.content.encode()).hexdigest()
            if actual != evidence.sha256:
                raise AdvisoryError(
                    f"evidence hash mismatch for {evidence.locator}"
                )

    def neutral_brief(self) -> dict[str, Any]:
        """Return only facts/constraints, never coordinator conclusions."""
        return {
            "protocol_version": self.protocol_version,
            "mode": self.mode,
            "requirement": self.requirement,
            "question": self.question,
            "evidence": [asdict(item) for item in self.evidence],
            "constraints": [asdict(item) for item in self.constraints],
            "review_target": asdict(self.review_target),
            "requested_output": (
                "Return JSON with findings, evidence locators, risks, unknowns "
                "and recommendation. Do not implement or modify files."
            ),
        }


@dataclass(frozen=True, slots=True)
class ProviderPerspective:
    provider: str
    status: str
    output: str
    stderr: str
    session_id: str | None
    duration_ms: int
    error_kind: str | None = None
    error: str | None = None


ProviderRunner = Callable[..., ProviderResult]


def run_advisory(
    request: AdvisoryRequest,
    repo: Path,
    *,
    runner: ProviderRunner = run_provider,
    dry_run: bool = False,
    artifact_dir: Path | None = None,
    run_id: str | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    request.validate()
    repo = repo.resolve()
    if not repo.is_dir():
        raise AdvisoryError(f"repository does not exist: {repo}")
    run_ref = run_id or f"advisory-{uuid.uuid4().hex[:12]}"
    brief = request.neutral_brief()
    serialized_brief = json.dumps(
        brief, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    brief_hash = hashlib.sha256(serialized_brief.encode()).hexdigest()
    prompt = _provider_prompt(serialized_brief)

    def invoke(provider: str) -> ProviderPerspective:
        started = monotonic()
        try:
            if dry_run:
                result = _dry_run_result(provider, request)
            else:
                result = runner(
                    provider_name=provider,
                    prompt=prompt,
                    repo=repo,
                    model=request.models.get(provider),
                    timeout_sec=request.timeout_sec,
                    sandbox_mode="read-only",
                    resume=request.resume,
                    allow_api_keys=False,
                )
            duration_ms = max(0, round((monotonic() - started) * 1000))
            return ProviderPerspective(
                provider=provider,
                status="completed",
                output=_redact(result.text_output()),
                stderr=_redact(result.stderr),
                session_id=_extract_session_id(result.stdout),
                duration_ms=duration_ms,
            )
        except ProviderQuotaError as exc:
            return _failure_perspective(
                provider, started, monotonic, "quota", exc.summary
            )
        except ProviderError as exc:
            return _failure_perspective(
                provider, started, monotonic, "provider", exc.summary
            )
        except Exception as exc:  # provider isolation: one slot cannot abort fan-out
            return _failure_perspective(
                provider, started, monotonic, "unexpected", str(exc)
            )

    by_provider: dict[str, ProviderPerspective] = {}
    with ThreadPoolExecutor(max_workers=len(request.providers)) as executor:
        futures = {
            executor.submit(invoke, provider): provider
            for provider in request.providers
        }
        for future in as_completed(futures):
            perspective = future.result()
            by_provider[perspective.provider] = perspective

    perspectives = [by_provider[name] for name in request.providers]
    completed = [item for item in perspectives if item.status == "completed"]
    completed_names = {item.provider for item in completed}
    quorum_met = len(completed) >= request.minimum_quorum
    required_met = set(request.required_providers).issubset(completed_names)
    status = "completed" if quorum_met and required_met else "needs_human"
    synthesis = (
        synthesize_perspectives(completed)
        if status == "completed"
        else {
            "agreements": [],
            "disagreements": [],
            "unique_findings": [],
            "summary": "Quorum or required provider failed; synthesis withheld.",
        }
    )
    result: dict[str, Any] = {
        "protocol_version": ADVISORY_PROTOCOL_VERSION,
        "run_id": run_ref,
        "status": status,
        "mode": request.mode,
        "brief_hash": brief_hash,
        "brief": brief,
        "quorum": {
            "minimum": request.minimum_quorum,
            "completed": len(completed),
            "met": quorum_met,
            "required_met": required_met,
        },
        "provider_runs": [asdict(item) for item in perspectives],
        "synthesis": synthesis,
    }
    if artifact_dir is not None:
        _write_result(artifact_dir, brief, result)
    return result


def synthesize_perspectives(
    perspectives: list[ProviderPerspective],
) -> dict[str, Any]:
    """Deterministic comparison that preserves every original perspective."""
    findings: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for perspective in perspectives:
        for index, item in enumerate(
            _extract_findings(perspective.output), start=1
        ):
            summary = str(item.get("summary") or item.get("finding") or "").strip()
            if not summary:
                continue
            finding = {
                "finding_id": (
                    "finding-"
                    + hashlib.sha256(
                        f"{perspective.provider}\0{index}\0{summary}".encode()
                    ).hexdigest()[:12]
                ),
                "provider": perspective.provider,
                "summary": summary,
                "category": str(item.get("category") or "general"),
                "severity": str(item.get("severity") or "unknown"),
                "evidence": _string_values(item.get("evidence")),
                "confidence": item.get("confidence"),
                "recommendation": str(item.get("recommendation") or ""),
                "disposition": "ESCALATE",
                "disposition_reason": "Awaiting explicit triage",
            }
            findings.append(finding)
            grouped[_normalize(summary)].append(finding)

    agreements = [
        {
            "summary": items[0]["summary"],
            "providers": sorted({str(item["provider"]) for item in items}),
            "finding_ids": [item["finding_id"] for item in items],
        }
        for items in grouped.values()
        if len({item["provider"] for item in items}) > 1
    ]
    unique = [
        item
        for items in grouped.values()
        if len({entry["provider"] for entry in items}) == 1
        for item in items
    ]
    providers = sorted({item.provider for item in perspectives})
    disagreements = (
        [
            {
                "providers": providers,
                "positions": [
                    {"provider": item.provider, "output": item.output}
                    for item in perspectives
                ],
            }
        ]
        if len(perspectives) > 1 and len(grouped) > 1
        else []
    )
    return {
        "agreements": sorted(agreements, key=lambda item: item["summary"]),
        "disagreements": disagreements,
        "unique_findings": unique,
        "findings": findings,
        "summary": (
            f"{len(perspectives)} independent perspective(s), "
            f"{len(agreements)} agreement group(s), "
            f"{len(unique)} unique finding(s)."
        ),
    }


def apply_dispositions(
    result: dict[str, Any],
    dispositions: dict[str, tuple[str, str]],
) -> dict[str, Any]:
    """Apply explicit CONFIRM/REJECT/ESCALATE triage without dropping findings."""
    findings = result.get("synthesis", {}).get("findings", [])
    if not isinstance(findings, list):
        raise AdvisoryError("result has no findings")
    known = {
        str(item.get("finding_id"))
        for item in findings
        if isinstance(item, dict)
    }
    if set(dispositions) - known:
        raise AdvisoryError("disposition references an unknown finding")
    for item in findings:
        if not isinstance(item, dict):
            continue
        finding_id = str(item.get("finding_id"))
        if finding_id not in dispositions:
            continue
        disposition, reason = dispositions[finding_id]
        if disposition not in DISPOSITIONS:
            raise AdvisoryError(f"invalid disposition: {disposition}")
        if not reason.strip():
            raise AdvisoryError("disposition reason must not be empty")
        item["disposition"] = disposition
        item["disposition_reason"] = reason.strip()
    return result


def _provider_prompt(serialized_brief: str) -> str:
    return (
        "Act as an independent advisory reviewer. You have not seen and must "
        "not infer another agent's opinion. Evaluate only the facts in the "
        "neutral brief. Do not modify files or implement a solution. Return "
        "JSON with a top-level `findings` array; every finding should contain "
        "summary, category, severity, evidence, confidence and recommendation. "
        "Also include risks and unknowns.\n\nNEUTRAL_BRIEF_JSON:\n"
        + serialized_brief
    )


def _dry_run_result(provider: str, request: AdvisoryRequest) -> ProviderResult:
    payload = {
        "session_id": f"dry-{provider}",
        "response": json.dumps(
            {
                "findings": [
                    {
                        "summary": f"Validate {request.mode} assumptions",
                        "category": request.mode,
                        "severity": "medium",
                        "evidence": [
                            item.locator for item in request.evidence[:1]
                        ],
                        "confidence": 0.7,
                        "recommendation": "Review with a human",
                    }
                ],
                "risks": ["dry-run"],
                "unknowns": [],
            }
        ),
    }
    return ProviderResult(provider, json.dumps(payload), "")


def _extract_findings(output: str) -> list[dict[str, Any]]:
    try:
        payload = extract_json_object(output)
    except ProviderError:
        return [{"summary": output}] if output.strip() else []
    raw = payload.get("findings")
    if not isinstance(raw, list):
        return [{"summary": output}] if output.strip() else []
    return [item for item in raw if isinstance(item, dict)]


def _extract_session_id(stdout: str) -> str | None:
    try:
        payload = json.loads(stdout.strip())
    except (json.JSONDecodeError, AttributeError):
        return None
    if isinstance(payload, dict) and isinstance(payload.get("session_id"), str):
        return str(payload["session_id"])
    return None


def _failure_perspective(
    provider: str,
    started: float,
    clock: Callable[[], float],
    kind: str,
    message: str,
) -> ProviderPerspective:
    return ProviderPerspective(
        provider=provider,
        status="failed",
        output="",
        stderr="",
        session_id=None,
        duration_ms=max(0, round((clock() - started) * 1000)),
        error_kind=kind,
        error=_redact(message),
    )


def _write_result(
    artifact_dir: Path,
    brief: dict[str, Any],
    result: dict[str, Any],
) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "brief.json").write_text(
        json.dumps(brief, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (artifact_dir / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _normalize(value: str) -> str:
    return _SPACE.sub(" ", value.strip().casefold())


def _redact(value: str) -> str:
    return _SECRET.sub(r"\1[REDACTED]", value or "")


def _required_text(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AdvisoryError(f"{key} must be a non-empty string")
    return value


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AdvisoryError(f"{field_name} must be a positive integer")
    return value


def _list(value: Any, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        raise AdvisoryError(f"{field_name} must be a list")
    return value


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AdvisoryError(f"{field_name} must be an object")
    return value


def _string_list(value: Any, field_name: str) -> list[str]:
    values = _list(value, field_name)
    if not all(isinstance(item, str) and item.strip() for item in values):
        raise AdvisoryError(f"{field_name} must contain non-empty strings")
    return [str(item) for item in values]


def _string_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str) and value:
        return [value]
    return []
