from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import re
import shutil
import subprocess
from pathlib import Path


DEFAULT_PROVIDER_COOLDOWN_SEC = 3600


class ProviderError(RuntimeError):
    pass


class ProviderQuotaError(ProviderError):
    def __init__(self, provider: str, retry_at: _dt.datetime | None, raw_message: str) -> None:
        super().__init__(raw_message or f"{provider} quota exhausted")
        self.provider = provider
        self.retry_at = retry_at
        self.raw_message = raw_message


_QUOTA_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "codex": [
        re.compile(r"\brate[\s_-]?limit", re.IGNORECASE),
        re.compile(r"\bquota\b", re.IGNORECASE),
        re.compile(r"\binsufficient[_ ]quota\b", re.IGNORECASE),
        re.compile(r"\busage[_ ]limit", re.IGNORECASE),
        re.compile(r"\b429\b"),
    ],
    "claude": [
        re.compile(r"\brate[\s_-]?limit", re.IGNORECASE),
        re.compile(r"\bcredit balance", re.IGNORECASE),
        re.compile(r"\busage[_ ]limit", re.IGNORECASE),
        re.compile(r"\b429\b"),
    ],
    "gemini": [
        re.compile(r"RESOURCE_EXHAUSTED", re.IGNORECASE),
        re.compile(r"\bquota\b", re.IGNORECASE),
        re.compile(r"\b429\b"),
    ],
}


_RETRY_AFTER_SECONDS = re.compile(
    r"(?:retry[- _]?after|try again in|reset in|retry in)[^\d]{0,12}(\d+)\s*(s|sec|secs|seconds|m|min|mins|minutes|h|hr|hrs|hours)?",
    re.IGNORECASE,
)
_RETRY_AT_TIMESTAMP = re.compile(
    r"(?:reset(?:s)?\s*at|retry\s*at|available\s*at)\s*[:=]?\s*(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)",
    re.IGNORECASE,
)
_RETRY_AT_EPOCH = re.compile(r"(?:retry[- _]?at|reset)\s*[:=]\s*(\d{10,13})", re.IGNORECASE)


def _parse_retry_at(message: str) -> _dt.datetime | None:
    if not message:
        return None
    now = _dt.datetime.now(_dt.timezone.utc)
    match = _RETRY_AFTER_SECONDS.search(message)
    if match:
        amount = int(match.group(1))
        unit = (match.group(2) or "s").lower()
        if unit.startswith("h"):
            seconds = amount * 3600
        elif unit.startswith("m") and not unit.startswith("ms"):
            seconds = amount * 60
        else:
            seconds = amount
        return now + _dt.timedelta(seconds=seconds)
    match = _RETRY_AT_TIMESTAMP.search(message)
    if match:
        raw = match.group(1).replace(" ", "T")
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = _dt.datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_dt.timezone.utc)
        return parsed
    match = _RETRY_AT_EPOCH.search(message)
    if match:
        epoch = int(match.group(1))
        if epoch > 10_000_000_000:
            epoch //= 1000
        return _dt.datetime.fromtimestamp(epoch, tz=_dt.timezone.utc)
    return None


def detect_provider_quota_error(
    *,
    provider: str,
    stdout: str,
    stderr: str,
    default_cooldown_sec: int = DEFAULT_PROVIDER_COOLDOWN_SEC,
) -> ProviderQuotaError | None:
    patterns = _QUOTA_PATTERNS.get(provider, [])
    if not patterns:
        return None
    haystack = "\n".join(part for part in (stderr, stdout) if part)
    if not any(p.search(haystack) for p in patterns):
        return None
    retry_at = _parse_retry_at(haystack)
    if retry_at is None:
        retry_at = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=default_cooldown_sec)
    return ProviderQuotaError(provider=provider, retry_at=retry_at, raw_message=haystack.strip())


@dataclasses.dataclass(slots=True)
class ProviderSpec:
    name: str
    command: list[str]
    supports_model: bool = True


@dataclasses.dataclass(slots=True)
class ProviderResult:
    provider: str
    stdout: str
    stderr: str

    def parse_json(self) -> dict[str, object]:
        text = self.stdout.strip()
        if not text:
            raise ProviderError(f"{self.provider} returned empty output")
        payload = extract_json_object(text)
        if not isinstance(payload, dict):
            raise ProviderError(f"{self.provider} returned unexpected JSON payload")
        return payload

    def text_output(self) -> str:
        return extract_text_output(self.stdout)


def extract_json_object(text: str) -> dict[str, object]:
    text = text.strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        fenced = re.findall(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
        candidates = fenced or _brace_candidates(text)
        for candidate in candidates:
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return normalize_payload_dict(payload)
        raise ProviderError("provider did not return valid JSON")
    if not isinstance(payload, dict):
        raise ProviderError("provider returned unexpected JSON payload")
    return normalize_payload_dict(payload)


def extract_text_output(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped
    extracted = _extract_text_from_payload(payload)
    return extracted.strip() if extracted else stripped


def _extract_text_from_payload(payload: object) -> str | None:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, list):
        parts = [_extract_text_from_payload(item) for item in payload]
        return "\n".join(part.strip() for part in parts if part and part.strip()) or None
    if not isinstance(payload, dict):
        return None

    for key in ("response", "content", "text", "result", "message"):
        value = payload.get(key)
        extracted = _extract_text_from_payload(value)
        if extracted and extracted.strip():
            return extracted

    candidates = payload.get("candidates")
    extracted = _extract_text_from_payload(candidates)
    if extracted and extracted.strip():
        return extracted

    parts = payload.get("parts")
    extracted = _extract_text_from_payload(parts)
    if extracted and extracted.strip():
        return extracted

    return None


def normalize_payload_dict(payload: dict[str, object]) -> dict[str, object]:
    for key in ("response", "content", "text", "result"):
        value = payload.get(key)
        if isinstance(value, str):
            try:
                nested = extract_json_object(value)
            except ProviderError:
                continue
            if isinstance(nested, dict):
                return nested
        if isinstance(value, dict):
            return normalize_payload_dict(value)
    return payload


def _brace_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    start: int | None = None
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
            continue
        if char == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(text[start : index + 1])
                start = None
    return candidates


PROVIDER_SPECS: dict[str, ProviderSpec] = {
    "codex": ProviderSpec(
        name="codex",
        command=["codex", "exec", "--sandbox", "workspace-write", "--skip-git-repo-check"],
    ),
    "claude": ProviderSpec(
        name="claude",
        command=["claude", "--print", "--output-format", "json", "--permission-mode", "acceptEdits"],
    ),
    "gemini": ProviderSpec(
        name="gemini",
        command=["gemini", "--output-format", "json", "--approval-mode", "auto_edit"],
    ),
}


def provider_available(name: str) -> bool:
    spec = PROVIDER_SPECS.get(name)
    return bool(spec and shutil.which(spec.command[0]))


def known_provider_names() -> set[str]:
    return set(PROVIDER_SPECS)


def available_providers(names: list[str]) -> list[str]:
    return [name for name in names if provider_available(name)]


def run_provider(
    provider_name: str,
    prompt: str,
    repo: Path,
    model: str | None,
    timeout_sec: int,
    cooldown_sec: int = DEFAULT_PROVIDER_COOLDOWN_SEC,
) -> ProviderResult:
    spec = PROVIDER_SPECS.get(provider_name)
    if spec is None:
        raise ProviderError(f"unknown provider: {provider_name}")
    if not provider_available(provider_name):
        raise ProviderError(f"provider binary is not available: {provider_name}")

    command = build_provider_command(
        provider_name=provider_name,
        base_command=spec.command,
        prompt=prompt,
        model=model,
        supports_model=spec.supports_model,
    )

    try:
        proc = subprocess.run(
            command,
            cwd=repo,
            text=True,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = "\n".join(
            part.strip()
            for part in (
                _timeout_output(exc.stdout),
                _timeout_output(exc.stderr),
            )
            if part and part.strip()
        )
        details = f": {output}" if output else ""
        raise ProviderError(f"{provider_name} timed out after {timeout_sec} seconds{details}") from exc
    if proc.returncode != 0:
        quota_exc = detect_provider_quota_error(
            provider=provider_name,
            stdout=proc.stdout,
            stderr=proc.stderr,
            default_cooldown_sec=cooldown_sec,
        )
        if quota_exc is not None:
            raise quota_exc
        raise ProviderError(proc.stderr.strip() or proc.stdout.strip() or f"{provider_name} failed")
    return ProviderResult(provider=provider_name, stdout=proc.stdout, stderr=proc.stderr)


def build_provider_command(
    *,
    provider_name: str,
    base_command: list[str],
    prompt: str,
    model: str | None,
    supports_model: bool,
) -> list[str]:
    command = list(base_command)
    if model and supports_model:
        command.extend(["--model", model])
    if provider_name == "gemini":
        command.extend(["--prompt", prompt])
    else:
        command.append(prompt)
    return command


def _timeout_output(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return ""
