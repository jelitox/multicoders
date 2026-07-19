from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import logging
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from multicoders import __version__
from multicoders.prompts import build_brainstorm_prompt, build_chat_prompt, build_discussion_prompt
from multicoders.providers import (
    DEFAULT_PROVIDER_COOLDOWN_SEC,
    ProviderError,
    ProviderQuotaError,
    available_providers,
    known_provider_names,
    run_provider,
)
from multicoders.stacks import StackProfile, detect_stack
from multicoders.storage import (
    claim_next_approved_task,
    connect_db,
    create_task,
    get_task,
    init_db,
    list_paused_quota_tasks,
    list_recent_tasks,
    resume_paused_task,
    retry_task,
    update_task_status,
)
from multicoders.telegram import TelegramBot, TelegramError, TelegramMessage

TELEGRAM_EXCEPTION_SUMMARY_LIMIT = 240
DEFAULT_AGENT_INTERNAL_LOG_MAX_BYTES = 10 * 1024 * 1024


_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)([\"']?\b[a-z0-9_/-]*(?:token|secret|password|api[_-]?key|access[_-]?key|private[_-]?key)[a-z0-9_/-]*[\"']?\s*[:=]\s*)([\"']?)[^\"'\s,}]+"
)
_AUTH_BEARER_RE = re.compile(r"(?i)\b(authorization\s*[:=]\s*bearer\s+)[a-z0-9._~+/=-]+")
_TELEGRAM_BOT_TOKEN_RE = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b")
_LLM_API_KEY_RE = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|sk-ant-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{20,})\b")


def redact_sensitive_text(text: str) -> str:
    if not text:
        return ""
    redacted = _SECRET_ASSIGNMENT_RE.sub(r"\1\2[REDACTED]", text)
    redacted = _AUTH_BEARER_RE.sub(r"\1[REDACTED]", redacted)
    redacted = _TELEGRAM_BOT_TOKEN_RE.sub("[REDACTED]", redacted)
    redacted = _LLM_API_KEY_RE.sub("[REDACTED]", redacted)
    return redacted


def short_exception_text(exc: BaseException, *, limit: int = TELEGRAM_EXCEPTION_SUMMARY_LIMIT) -> str:
    summary = getattr(exc, "summary", None)
    if isinstance(summary, str) and summary.strip():
        first_line = summary.strip().splitlines()[0]
    else:
        first_line = next((line.strip() for line in str(exc).splitlines() if line.strip()), "")
    if not first_line:
        first_line = exc.__class__.__name__
    if len(first_line) <= limit:
        return first_line
    return first_line[: limit - 3].rstrip() + "..."


def log_exception_details(exc: BaseException) -> None:
    raw_stderr = getattr(exc, "raw_stderr", "")
    raw_stdout = getattr(exc, "raw_stdout", "")
    if isinstance(raw_stderr, str) and raw_stderr.strip():
        LOGGER.debug("provider raw stderr: %s", raw_stderr)
    if isinstance(raw_stdout, str) and raw_stdout.strip():
        LOGGER.debug("provider raw stdout: %s", raw_stdout)
    LOGGER.debug("exception traceback", exc_info=exc)

ENV_FILE_NAME = ".env"
DEFAULT_PROVIDERS = ["codex", "claude", "gemini"]
SERVICE_POLL_SEC = 5
TASK_TYPES = {"bugfix", "feature"}
LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
AUTONOMOUS_CHAT_TRANSCRIPT_LIMIT = 30
CHAT_PROVIDER_TIMEOUT_SEC = 600
PROVIDER_COOLDOWN_SEC = DEFAULT_PROVIDER_COOLDOWN_SEC
PROVIDER_SLEEP_PENDING_LIMIT = 20
PROVIDER_SLEEP_TRANSCRIPT_TAIL = 6
BRAINSTORMING_MAX_ROUNDS = 3
SILENCE_WORDS = {"silencio", "/silencio", "silence", "/silence", "/stop-chat"}
TEXT_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".css",
    ".env",
    ".go",
    ".h",
    ".html",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".md",
    ".mjs",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".next",
    ".idea",
    ".vscode",
}


class MulticodersError(RuntimeError):
    pass


@dataclass(slots=True)
class RepoContext:
    repo: Path
    branch: str
    recent_commit: str
    stack: StackProfile
    candidate_files: list[str]


@dataclass(slots=True)
class AgentConfig:
    provider: str
    display_name: str
    model: str | None
    bot: TelegramBot | None


@dataclass(slots=True)
class ValidationResult:
    command: str
    ok: bool
    output: str


@dataclass(slots=True)
class HumanFeedback:
    messages: list[TelegramMessage]
    baseline_update_id: int


@dataclass(slots=True)
class RichMediaRequest:
    kind: str
    key: str
    asset: str
    caption: str | None = None


@dataclass(slots=True)
class RichChatResponse:
    text: str
    media: list[RichMediaRequest]


@dataclass(slots=True)
class BrainstormProposal:
    proposal_id: str
    author: str
    summary: str
    approach: str
    self_score: int
    risks: list[str]
    improvement_ideas: list[str]


@dataclass(slots=True)
class BrainstormScorecard:
    scorer: str
    scores: dict[str, int]
    best_proposal_id: str
    summary: str
    reasons: list[str]


@dataclass(slots=True)
class BrainstormImprovement:
    improvement_id: str
    author: str
    target_solution_id: str
    summary: str
    improvement: str
    self_score: int
    tradeoffs: list[str]


@dataclass(slots=True)
class BrainstormSpec:
    spec_title: str
    spec_markdown: str
    summary: str
    file_name: str


@dataclass(slots=True)
class TelegramState:
    state_file: Path
    last_update_id: int = 0
    bot_offsets: dict[str, int] | None = None
    last_run_id: str = ""
    last_repo: str = ""
    last_task: str = ""
    last_feedback_count: int = 0
    last_started_at: str = ""
    last_finished_at: str = ""
    discussion_runs: list[dict[str, object]] | None = None
    recent_message_keys: list[str] | None = None
    bot_chat: dict[str, object] | None = None
    brainstorming: dict[str, object] | None = None
    provider_sleeps: dict[str, dict[str, object]] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "last_update_id": self.last_update_id,
            "bot_offsets": self.bot_offsets or {},
            "last_run_id": self.last_run_id,
            "last_repo": self.last_repo,
            "last_task": self.last_task,
            "last_feedback_count": self.last_feedback_count,
            "last_started_at": self.last_started_at,
            "last_finished_at": self.last_finished_at,
            "discussion_runs": self.discussion_runs or [],
            "recent_message_keys": self.recent_message_keys or [],
            "bot_chat": self.bot_chat or {},
            "brainstorming": self.brainstorming or {},
            "provider_sleeps": self.provider_sleeps or {},
        }


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = strip_env_inline_comment(value.strip()).strip().strip("\"").strip("'")
        os.environ.setdefault(key, value)


def strip_env_inline_comment(value: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            continue
        if char == "#" and not in_single and not in_double:
            if index == 0 or value[index - 1].isspace():
                return value[:index].rstrip()
    return value


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


LOGGER = logging.getLogger("multicoders")


def configure_logging(*, level_name: str, log_file: str | None) -> None:
    normalized_level = level_name.upper()
    if normalized_level not in LOG_LEVELS:
        raise SystemExit(f"Invalid log level: {level_name}. Expected one of: {', '.join(sorted(LOG_LEVELS))}")
    level = getattr(logging, normalized_level)
    LOGGER.setLevel(level)
    LOGGER.handlers.clear()

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    LOGGER.addHandler(stream_handler)

    if log_file:
        log_path = Path(log_file).expanduser().resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        LOGGER.addHandler(file_handler)

    LOGGER.propagate = False


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def parse_provider_names(raw_providers: str | list[str]) -> list[str]:
    names = (
        [item.strip() for item in raw_providers.split(",") if item.strip()]
        if isinstance(raw_providers, str)
        else [str(item).strip() for item in raw_providers if str(item).strip()]
    )
    unknown = [name for name in names if name not in known_provider_names()]
    if unknown:
        raise SystemExit(
            "Unknown provider(s): "
            + ", ".join(unknown)
            + ". Expected any of: "
            + ", ".join(sorted(known_provider_names()))
        )
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise SystemExit(f"Provider names must be unique. Duplicates: {', '.join(duplicates)}")
    return names


def parse_optional_env_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise MulticodersError(f"{name} must be an integer") from exc
    if value < 0:
        raise MulticodersError(f"{name} must be zero or greater")
    return value


def slugify_text(text: str, *, max_length: int = 48) -> str:
    normalized = []
    previous_dash = False
    for char in text.lower():
        if char.isalnum():
            normalized.append(char)
            previous_dash = False
        elif not previous_dash:
            normalized.append("-")
            previous_dash = True
    slug = "".join(normalized).strip("-")
    return slug[:max_length].strip("-") or "brainstorming"


def parse_media_catalog(raw: str | None) -> dict[str, str]:
    if raw is None or not raw.strip():
        return {}
    text = raw.strip()
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise MulticodersError("media asset catalog must be JSON object or key=value list") from exc
        if not isinstance(payload, dict):
            raise MulticodersError("media asset catalog JSON must be an object")
        return {
            str(key).strip().lower(): str(value).strip()
            for key, value in payload.items()
            if str(key).strip() and str(value).strip()
        }
    assets: dict[str, str] = {}
    for item in text.split(","):
        part = item.strip()
        if not part:
            continue
        if "=" not in part:
            raise MulticodersError(f"invalid media asset entry: {part}")
        key, value = part.split("=", 1)
        normalized_key = key.strip().lower()
        normalized_value = value.strip()
        if normalized_key and normalized_value:
            assets[normalized_key] = normalized_value
    return assets


def load_media_catalog(kind: str) -> dict[str, str]:
    env_names = {
        "gif": ("MULTICODERS_GIF_ASSETS", "TELEGRAM_GIF_ASSETS"),
        "meme": ("MULTICODERS_MEME_ASSETS", "TELEGRAM_MEME_ASSETS"),
        "sticker": ("MULTICODERS_STICKER_ASSETS", "TELEGRAM_STICKER_ASSETS"),
    }
    for env_name in env_names.get(kind, ()):
        catalog = parse_media_catalog(os.environ.get(env_name))
        if catalog:
            return catalog
    return {}


def media_catalogs() -> dict[str, dict[str, str]]:
    return {
        "gif": load_media_catalog("gif"),
        "meme": load_media_catalog("meme"),
        "sticker": load_media_catalog("sticker"),
    }


def brainstorming_docs_dir(repo: Path) -> Path:
    return repo / "docs" / "brainstorming"


def format_chat_media_capabilities() -> str:
    catalogs = media_catalogs()
    lines = [
        "Rich media directives:",
        "- You may use normal emoji/emoticons directly in your text when they help tone.",
        "- To request rich media, put one directive on its own line: [gif:key], [meme:key], [sticker:key], or [emoji:text].",
        "- Use only the exact configured keys listed below. If no key exists for a media type, do not request that type.",
    ]
    for kind in ("gif", "meme", "sticker"):
        keys = sorted(catalogs[kind])
        label = {"gif": "GIF", "meme": "meme", "sticker": "sticker"}[kind]
        lines.append(f"- Available {label} keys: {', '.join(keys) if keys else '(none configured)'}")
    return "\n".join(lines)


def load_telegram_state(path: Path) -> TelegramState:
    if not path.exists():
        return TelegramState(state_file=path, discussion_runs=[])
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MulticodersError(f"failed to read telegram state file: {path}") from exc
    if not isinstance(payload, dict):
        raise MulticodersError(f"telegram state file has invalid format: {path}")
    runs = payload.get("discussion_runs")
    bot_offsets = payload.get("bot_offsets")
    recent_message_keys = payload.get("recent_message_keys")
    bot_chat = payload.get("bot_chat")
    brainstorming = payload.get("brainstorming")
    provider_sleeps = payload.get("provider_sleeps")
    return TelegramState(
        state_file=path,
        last_update_id=int(payload.get("last_update_id", 0) or 0),
        bot_offsets=bot_offsets if isinstance(bot_offsets, dict) else {},
        last_run_id=str(payload.get("last_run_id", "")),
        last_repo=str(payload.get("last_repo", "")),
        last_task=str(payload.get("last_task", "")),
        last_feedback_count=int(payload.get("last_feedback_count", 0) or 0),
        last_started_at=str(payload.get("last_started_at", "")),
        last_finished_at=str(payload.get("last_finished_at", "")),
        discussion_runs=runs if isinstance(runs, list) else [],
        recent_message_keys=recent_message_keys if isinstance(recent_message_keys, list) else [],
        bot_chat=bot_chat if isinstance(bot_chat, dict) and bot_chat else None,
        brainstorming=brainstorming if isinstance(brainstorming, dict) and brainstorming else None,
        provider_sleeps=provider_sleeps if isinstance(provider_sleeps, dict) and provider_sleeps else None,
    )


def save_telegram_state(state: TelegramState) -> None:
    state.state_file.parent.mkdir(parents=True, exist_ok=True)
    state.state_file.write_text(
        json.dumps(state.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _parse_iso_datetime(value: object) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    raw = value
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def provider_sleep_until(state: TelegramState, provider: str) -> dt.datetime | None:
    sleeps = state.provider_sleeps or {}
    entry = sleeps.get(provider)
    if not isinstance(entry, dict):
        return None
    return _parse_iso_datetime(entry.get("until"))


def is_provider_sleeping(state: TelegramState, provider: str) -> bool:
    until = provider_sleep_until(state, provider)
    if until is None:
        return False
    return until > dt.datetime.now(dt.timezone.utc)


def mark_provider_sleeping(
    state: TelegramState,
    *,
    provider: str,
    until: dt.datetime,
    reason: str,
) -> None:
    sleeps = dict(state.provider_sleeps or {})
    existing = sleeps.get(provider) if isinstance(sleeps.get(provider), dict) else {}
    pending = existing.get("pending") if isinstance(existing, dict) else []
    if not isinstance(pending, list):
        pending = []
    sleeps[provider] = {
        "until": until.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "since": utc_now_iso(),
        "reason": reason[:500],
        "pending": pending,
    }
    state.provider_sleeps = sleeps


def record_pending_for_provider(
    state: TelegramState,
    *,
    provider: str,
    sender_name: str,
    user_message: str,
    transcript_tail: list[dict[str, str]],
) -> None:
    sleeps = state.provider_sleeps or {}
    entry = sleeps.get(provider)
    if not isinstance(entry, dict):
        return
    pending = entry.get("pending")
    if not isinstance(pending, list):
        pending = []
    pending.append(
        {
            "at": utc_now_iso(),
            "sender_name": sender_name,
            "user_message": user_message[:2000],
            "transcript_tail": transcript_tail[-PROVIDER_SLEEP_TRANSCRIPT_TAIL:],
        }
    )
    if len(pending) > PROVIDER_SLEEP_PENDING_LIMIT:
        pending = pending[-PROVIDER_SLEEP_PENDING_LIMIT:]
    entry["pending"] = pending
    sleeps[provider] = entry
    state.provider_sleeps = sleeps


def wake_provider(state: TelegramState, provider: str) -> dict[str, object] | None:
    sleeps = dict(state.provider_sleeps or {})
    entry = sleeps.pop(provider, None)
    state.provider_sleeps = sleeps or None
    if isinstance(entry, dict):
        return entry
    return None


def format_sleep_duration(until: dt.datetime) -> str:
    delta = until - dt.datetime.now(dt.timezone.utc)
    seconds = max(int(delta.total_seconds()), 0)
    if seconds >= 3600:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"~{hours}h{minutes:02d}m"
    if seconds >= 60:
        return f"~{seconds // 60}m"
    return f"~{seconds}s"


def build_provider_catchup_note(sleep_entry: dict[str, object]) -> str:
    pending_raw = sleep_entry.get("pending") if isinstance(sleep_entry, dict) else None
    pending: list[dict[str, object]] = pending_raw if isinstance(pending_raw, list) else []
    since = sleep_entry.get("since") if isinstance(sleep_entry, dict) else None
    reason = sleep_entry.get("reason") if isinstance(sleep_entry, dict) else None
    lines = ["You were temporarily offline (rate limit / quota)."]
    if isinstance(since, str) and since:
        lines.append(f"Offline since: {since}")
    if isinstance(reason, str) and reason:
        lines.append(f"Reason captured from API: {reason[:200]}")
    if pending:
        lines.append(f"Messages you missed while offline ({len(pending)}):")
        for index, item in enumerate(pending[-PROVIDER_SLEEP_PENDING_LIMIT:], start=1):
            if not isinstance(item, dict):
                continue
            sender = str(item.get("sender_name") or "alguien")
            text = str(item.get("user_message") or "")
            tail_raw = item.get("transcript_tail")
            tail = tail_raw if isinstance(tail_raw, list) else []
            lines.append(f"{index}. [{sender}] {text[:400]}")
            for turn in tail:
                if not isinstance(turn, dict):
                    continue
                speaker = str(turn.get("speaker") or "?")
                turn_text = str(turn.get("text") or "")
                if turn_text:
                    lines.append(f"   - {speaker}: {turn_text[:200]}")
    else:
        lines.append("No new messages were captured while you were offline.")
    lines.append(
        "Start your reply with one short paragraph summarizing what you missed and how you'd catch up, then continue normally."
    )
    return "\n".join(lines)


def handle_provider_wake(
    *,
    state: TelegramState,
    agent: AgentConfig,
    observer_bot: TelegramBot | None,
    dry_run: bool,
    run_id: str | None,
    conn=None,
) -> str | None:
    until = provider_sleep_until(state, agent.provider)
    if until is None:
        return None
    if until > dt.datetime.now(dt.timezone.utc):
        return None
    entry = wake_provider(state, agent.provider)
    save_telegram_state(state)
    if entry is None:
        return None
    pending_count = len(entry.get("pending") or []) if isinstance(entry.get("pending"), list) else 0
    LOGGER.info(
        "provider woke up provider=%s pending=%d run_id=%s",
        agent.provider,
        pending_count,
        run_id or "",
    )
    send_service_message(
        observer_bot,
        f"[multicoders-service] {agent.display_name} volvió. Le paso un resumen de lo que se perdió ({pending_count} mensaje(s)).",
        dry_run,
        run_id=run_id,
    )
    if conn is not None:
        resume_tasks_waiting_for_provider(
            conn,
            provider=agent.provider,
            observer_bot=observer_bot,
            dry_run=dry_run,
        )
    if resume_brainstorming_if_waiting_for(state, provider=agent.provider):
        save_telegram_state(state)
        send_service_message(
            observer_bot,
            f"[multicoders-service] {agent.display_name} volvió. Reanudo el brainstorming.",
            dry_run,
        )
    return build_provider_catchup_note(entry)


def handle_provider_quota_failure(
    *,
    state: TelegramState | None,
    agent: AgentConfig,
    exc: ProviderQuotaError,
    sender_name: str,
    user_message: str,
    transcript_tail: list[dict[str, str]],
    observer_bot: TelegramBot | None,
    dry_run: bool,
    run_id: str | None,
) -> None:
    until = exc.retry_at or (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=PROVIDER_COOLDOWN_SEC))
    LOGGER.warning(
        "provider quota exhausted provider=%s until=%s run_id=%s",
        agent.provider,
        until.isoformat(),
        run_id or "",
    )
    if state is not None:
        mark_provider_sleeping(
            state,
            provider=agent.provider,
            until=until,
            reason=exc.raw_message or "quota exhausted",
        )
        record_pending_for_provider(
            state,
            provider=agent.provider,
            sender_name=sender_name,
            user_message=user_message,
            transcript_tail=transcript_tail,
        )
        save_telegram_state(state)
    until_local = until.astimezone().strftime("%H:%M")
    duration = format_sleep_duration(until)
    send_service_message(
        observer_bot,
        (
            f"[multicoders-service] {agent.display_name} se quedó sin tokens. "
            f"Lo despierto a las {until_local} ({duration}). Mientras tanto sigo con los demás."
        ),
        dry_run,
        run_id=run_id,
    )


def resume_tasks_waiting_for_provider(
    conn,
    *,
    provider: str,
    observer_bot: TelegramBot | None,
    dry_run: bool,
) -> list[int]:
    resumed: list[int] = []
    for task in list_paused_quota_tasks(conn):
        meta = _paused_quota_meta(task)
        waiting_on = str(meta.get("paused_provider") or "")
        if waiting_on and waiting_on != provider:
            continue
        if resume_paused_task(conn, task_id=task.id, updated_at=utc_now_iso()):
            resumed.append(task.id)
            send_service_message(
                observer_bot,
                f"[multicoders-service] {provider} volvió. Reanudo #{task.id}.",
                dry_run,
                run_id=f"task-{task.id}",
            )
    return resumed


def _paused_quota_meta(task) -> dict[str, object]:
    raw = getattr(task, "result_json", None)
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    meta = payload.get("paused_quota")
    return meta if isinstance(meta, dict) else {}


def resume_brainstorming_if_waiting_for(state: TelegramState, *, provider: str) -> bool:
    session = state.brainstorming if isinstance(state.brainstorming, dict) else None
    if not session:
        return False
    paused = session.get("paused_quota") if isinstance(session.get("paused_quota"), dict) else None
    if not paused:
        return False
    waiting_on = str(paused.get("provider") or "")
    if waiting_on and waiting_on != provider:
        return False
    session.pop("paused_quota", None)
    state.brainstorming = session
    return True


def format_quorum_status(
    state: TelegramState,
    *,
    providers: list[str],
    agents: list[AgentConfig],
) -> str:
    name_by_provider = {agent.provider: agent.display_name for agent in agents}
    parts: list[str] = []
    for provider in providers:
        display = name_by_provider.get(provider, provider)
        until = provider_sleep_until(state, provider)
        if until is not None and until > dt.datetime.now(dt.timezone.utc):
            entry = (state.provider_sleeps or {}).get(provider, {}) if isinstance(state.provider_sleeps, dict) else {}
            pending_raw = entry.get("pending") if isinstance(entry, dict) else None
            pending_count = len(pending_raw) if isinstance(pending_raw, list) else 0
            until_local = until.astimezone().strftime("%H:%M")
            duration = format_sleep_duration(until)
            parts.append(f"{display}: dormido hasta {until_local} ({duration}, {pending_count} pendiente(s))")
        else:
            parts.append(f"{display}: ok")
    awake = sum(1 for provider in providers if not is_provider_sleeping(state, provider))
    return f"[multicoders-service] quórum {awake}/{len(providers)} · " + " · ".join(parts)


def parse_quorum_command(text: str) -> bool:
    return matches_simple_command(text, "quorum")


def parse_wake_command(text: str) -> str | None:
    stripped = text.strip()
    if not (stripped.startswith("/wake ") or stripped.startswith("/wake@")):
        return None
    if stripped.startswith("/wake@"):
        parts = stripped.split(maxsplit=1)
        if len(parts) != 2:
            return None
        remainder = parts[1].strip()
    else:
        remainder = stripped[len("/wake ") :].strip()
    name = remainder.split()[0].lower() if remainder else ""
    return name or None


def parse_promote_command(text: str) -> tuple[str, int] | None:
    stripped = text.strip()
    if not (stripped.startswith("/promote ") or stripped.startswith("/promote@")):
        return None
    if stripped.startswith("/promote@"):
        parts = stripped.split(maxsplit=1)
        if len(parts) != 2:
            return None
        remainder = parts[1].strip()
    else:
        remainder = stripped[len("/promote ") :].strip()
    pieces = remainder.split()
    if len(pieces) < 2:
        return None
    provider = pieces[0].strip().lower()
    raw_id = pieces[1].lstrip("#")
    try:
        task_id = int(raw_id)
    except ValueError:
        return None
    return provider, task_id


def force_wake_provider(
    state: TelegramState,
    *,
    provider: str,
    observer_bot: TelegramBot | None,
    dry_run: bool,
    conn=None,
) -> bool:
    entry = wake_provider(state, provider)
    if entry is None and not is_provider_sleeping(state, provider):
        send_service_message(
            observer_bot,
            f"[multicoders-service] {provider} ya está despierto, nada que hacer.",
            dry_run,
        )
        return False
    save_telegram_state(state)
    pending = entry.get("pending") if isinstance(entry, dict) else None
    pending_count = len(pending) if isinstance(pending, list) else 0
    send_service_message(
        observer_bot,
        f"[multicoders-service] {provider} despertado manualmente. {pending_count} pendiente(s) registrado(s).",
        dry_run,
    )
    if conn is not None:
        resume_tasks_waiting_for_provider(
            conn,
            provider=provider,
            observer_bot=observer_bot,
            dry_run=dry_run,
        )
    resume_brainstorming_if_waiting_for(state, provider=provider)
    return True


def parse_task_command(text: str) -> tuple[str, str, str] | None:
    if text.startswith("/task "):
        remainder = text[len("/task ") :].strip()
    elif text.startswith("/task@"):
        parts = text.split(maxsplit=1)
        if len(parts) != 2:
            return None
        remainder = parts[1].strip()
    else:
        return None
    return _parse_task_remainder(remainder)


def parse_random_task_command(text: str) -> tuple[str, str, str] | None:
    if text.startswith("/random-task "):
        remainder = text[len("/random-task ") :].strip()
    elif text.startswith("/random-task@"):
        parts = text.split(maxsplit=1)
        if len(parts) != 2:
            return None
        remainder = parts[1].strip()
    else:
        return None
    return _parse_task_remainder(remainder)


def parse_chat_command(text: str) -> str | None:
    if text.startswith("/chat "):
        remainder = text[len("/chat ") :].strip()
    elif text.startswith("/chat@"):
        parts = text.split(maxsplit=1)
        if len(parts) != 2:
            return None
        remainder = parts[1].strip()
    else:
        return None
    return remainder or None


def parse_brainstorming_command(text: str) -> str | None:
    if text.startswith("/brainstorming "):
        remainder = text[len("/brainstorming ") :].strip()
    elif text.startswith("/brainstorming@"):
        parts = text.split(maxsplit=1)
        if len(parts) != 2:
            return None
        remainder = parts[1].strip()
    else:
        return None
    return remainder or None


def parse_brainstorming_status_command(text: str) -> bool:
    return matches_simple_command(text, "brainstorming-status")


def parse_brainstorming_cancel_command(text: str) -> bool:
    return matches_simple_command(text, "brainstorming-cancel")


def matches_brainstorming_status_command(text: str) -> bool:
    return parse_brainstorming_status_command(text)


def matches_brainstorming_cancel_command(text: str) -> bool:
    return parse_brainstorming_cancel_command(text)


def parse_silence_command(text: str) -> bool:
    stripped = text.strip().lower()
    if not stripped:
        return False
    if stripped in SILENCE_WORDS:
        return True
    if matches_brainstorming_cancel_command(text):
        return True
    if matches_simple_command(stripped, "silencio"):
        return True
    normalized = stripped.strip(" .!¡?¿")
    return normalized == "silencio"


def parse_media_command(text: str) -> tuple[str, str, str | None] | None:
    stripped = text.strip()
    for kind in ("gif", "meme", "sticker"):
        prefix = f"/{kind} "
        alt_prefix = f"/{kind}@"
        if stripped.startswith(prefix):
            remainder = stripped[len(prefix) :].strip()
        elif stripped.startswith(alt_prefix):
            parts = stripped.split(maxsplit=1)
            if len(parts) != 2:
                return None
            remainder = parts[1].strip()
        else:
            continue
        try:
            pieces = shlex.split(remainder)
        except ValueError:
            return None
        if not pieces:
            return None
        key = pieces[0].strip().lower()
        caption = " ".join(pieces[1:]).strip() or None
        return kind, key, caption
    return None


def _parse_task_remainder(remainder: str) -> tuple[str, str, str] | None:
    repo_token = None
    type_token = "bugfix"
    body_parts: list[str] = []
    try:
        tokens = shlex.split(remainder)
    except ValueError:
        return None
    for token in tokens:
        if token.startswith("repo=") and repo_token is None:
            repo_token = token.split("=", 1)[1].strip()
            continue
        if token.startswith("type="):
            type_token = (token.split("=", 1)[1].strip() or "bugfix").lower()
            continue
        body_parts.append(token)
    task_text = " ".join(body_parts).strip()
    if not repo_token or not task_text or type_token not in TASK_TYPES:
        return None
    return repo_token, type_token, task_text


def parse_integer_command(text: str, command: str) -> int | None:
    prefix = f"/{command} "
    alt_prefix = f"/{command}@"
    if text.startswith(prefix):
        raw = text[len(prefix) :].strip()
    elif text.startswith(alt_prefix):
        parts = text.split(maxsplit=1)
        if len(parts) != 2:
            return None
        raw = parts[1].strip()
    else:
        return None
    if not raw.isdigit():
        return None
    return int(raw)


def matches_simple_command(text: str, command: str) -> bool:
    stripped = text.strip()
    return stripped == f"/{command}" or stripped.startswith(f"/{command}@")


def append_discussion_run(
    state: TelegramState,
    *,
    run_id: str,
    repo: Path,
    task: str,
    started_at: str,
    finished_at: str,
    winner: str | None,
    feedback_messages: list[TelegramMessage],
) -> None:
    runs = list(state.discussion_runs or [])
    runs.append(
        {
            "run_id": run_id,
            "repo": str(repo),
            "task": task,
            "started_at": started_at,
            "finished_at": finished_at,
            "winner": winner or "",
            "feedback_count": len(feedback_messages),
            "feedback_messages": [
                {"sender": item.sender_name, "text": item.text, "update_id": item.update_id}
                for item in feedback_messages
            ],
        }
    )
    state.discussion_runs = runs[-20:]


def message_fingerprint(message: TelegramMessage) -> str:
    if message.message_id is not None:
        return "|".join(
            [
                message.chat_id,
                str(message.message_thread_id or ""),
                str(message.message_id),
            ]
        )
    return "|".join(
        [
            message.chat_id,
            message.sender_id,
            str(message.date),
            str(message.message_thread_id or ""),
            message.text.strip(),
        ]
    )


def first_available_agent(agents: list[AgentConfig], state: TelegramState | None = None) -> AgentConfig | None:
    for agent in agents:
        if state is not None and is_provider_sleeping(state, agent.provider):
            continue
        return agent
    return agents[0] if agents else None


def remember_message_key(state: TelegramState, key: str, limit: int = 100) -> None:
    keys = list(state.recent_message_keys or [])
    keys.append(key)
    state.recent_message_keys = keys[-limit:]


def iter_listener_bots(service_bot: TelegramBot | None, agents: list[AgentConfig]) -> list[TelegramBot]:
    listeners: list[TelegramBot] = []
    seen: set[str] = set()
    for bot in [service_bot, *[agent.bot for agent in agents]]:
        if bot is None:
            continue
        identity = getattr(bot, "name", "") or getattr(bot, "token", "") or str(id(bot))
        if identity in seen:
            continue
        seen.add(identity)
        listeners.append(bot)
    return listeners


def message_matches_listener_scope(message: TelegramMessage, listener: TelegramBot) -> bool:
    if message.chat_id != listener.chat_id:
        return False
    listener_thread_id = getattr(listener, "message_thread_id", None)
    if listener_thread_id is None:
        return True
    return message.message_thread_id == listener_thread_id


def run_git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise MulticodersError(proc.stderr.strip() or proc.stdout.strip() or "git command failed")
    return proc.stdout.strip()


def collect_candidate_files(repo: Path, limit: int = 24) -> list[str]:
    result: list[str] = []
    for current_root, dir_names, file_names in os.walk(repo):
        dir_names[:] = sorted(name for name in dir_names if name not in IGNORED_DIRS)
        current = Path(current_root)
        for file_name in sorted(file_names):
            path = current / file_name
            if not path.is_file():
                continue
            if path.suffix.lower() not in TEXT_EXTENSIONS:
                continue
            try:
                if path.stat().st_size > 256 * 1024:
                    continue
            except OSError:
                continue
            result.append(str(path.relative_to(repo)))
            if len(result) >= limit:
                return result
    return result


def build_repo_context(repo: Path, *, allow_non_git: bool = False) -> RepoContext:
    try:
        resolved_repo = Path(run_git(repo, "rev-parse", "--show-toplevel")).resolve()
    except MulticodersError as exc:
        if allow_non_git:
            resolved_repo = repo.resolve()
            if not resolved_repo.is_dir():
                raise MulticodersError(f"{repo} is not a directory") from exc
            return RepoContext(
                repo=resolved_repo,
                branch="no-git",
                recent_commit="no commits",
                stack=detect_stack(resolved_repo),
                candidate_files=collect_candidate_files(resolved_repo),
            )
        raise MulticodersError(f"{repo} is not a git repository") from exc
    branch = run_git(resolved_repo, "branch", "--show-current") or "HEAD"
    recent_commit = run_git(resolved_repo, "log", "-1", "--pretty=%h %s", check=False) or "no commits"
    stack = detect_stack(resolved_repo)
    candidate_files = collect_candidate_files(resolved_repo)
    return RepoContext(
        repo=resolved_repo,
        branch=branch,
        recent_commit=recent_commit,
        stack=stack,
        candidate_files=candidate_files,
    )


def build_agent_configs(args: argparse.Namespace) -> list[AgentConfig]:
    model_by_provider = {
        "codex": getattr(args, "codex_model", None),
        "claude": getattr(args, "claude_model", None),
        "gemini": getattr(args, "gemini_model", None),
    }
    env_mapping = {
        "codex": ("BOT_CODEX", "BOT_CODEX_KEY"),
        "claude": ("BOT_CLAUDE", "BOT_CLAUDE_KEY"),
        "gemini": ("BOT_GEMINI", "BOT_GEMINI_KEY"),
    }
    chat_id = os.environ.get("TELEGRAM_GROUP")
    topic_id = parse_optional_env_int("TELEGRAM_TOPIC_ID")

    configs: list[AgentConfig] = []
    for provider in args.providers:
        display_var, token_var = env_mapping[provider]
        display_name = os.environ.get(display_var, provider)
        token = os.environ.get(token_var)
        bot = None
        if token and chat_id and not getattr(args, "no_telegram", False):
            bot = TelegramBot(name=display_name, token=token, chat_id=chat_id, message_thread_id=topic_id)
        configs.append(
            AgentConfig(
                provider=provider,
                display_name=display_name,
                model=model_by_provider.get(provider),
                bot=bot,
            )
        )
    return configs


def annotate_message(text: str, run_id: str | None = None) -> str:
    if not run_id:
        return text
    return f"[run:{run_id}] {text}"


def preview_text(text: str, limit: int = 160) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def send_group_message(agent: AgentConfig, text: str, dry_run: bool, run_id: str | None = None) -> None:
    rendered = redact_sensitive_text(annotate_message(text, run_id))
    if dry_run:
        LOGGER.debug("telegram send skipped mode=dry-run kind=group provider=%s text=%s", agent.provider, preview_text(rendered))
        return
    if agent.bot is None:
        LOGGER.debug("telegram send skipped mode=no-bot kind=group provider=%s text=%s", agent.provider, preview_text(rendered))
        return
    bot_name = getattr(agent.bot, "name", agent.display_name)
    LOGGER.debug(
        "telegram send attempt kind=group provider=%s bot=%s chat_id=%s thread_id=%s text=%s",
        agent.provider,
        bot_name,
        getattr(agent.bot, "chat_id", ""),
        getattr(agent.bot, "message_thread_id", None),
        preview_text(rendered),
    )
    try:
        agent.bot.send_message(rendered)
    except TelegramError as exc:
        LOGGER.error("telegram send failed kind=group provider=%s bot=%s error=%s", agent.provider, bot_name, exc)
        raise MulticodersError(str(exc)) from exc
    LOGGER.debug("telegram send ok kind=group provider=%s bot=%s", agent.provider, bot_name)


def post_phase_header(agents: list[AgentConfig], title: str, dry_run: bool, run_id: str | None = None) -> None:
    header = f"[multicoders] {phase_header_text(title)}"
    for agent in agents:
        send_group_message(agent, header, dry_run, run_id=run_id)


def select_observer_bot(agents: list[AgentConfig]) -> TelegramBot | None:
    for agent in agents:
        if agent.bot is not None:
            return agent.bot
    return None


def send_service_message(bot: TelegramBot | None, text: str, dry_run: bool, run_id: str | None = None) -> None:
    rendered = redact_sensitive_text(annotate_message(text, run_id))
    if dry_run:
        LOGGER.debug("telegram send skipped mode=dry-run kind=service text=%s", preview_text(rendered))
        return
    if bot is None:
        LOGGER.debug("telegram send skipped mode=no-bot kind=service text=%s", preview_text(rendered))
        return
    bot_name = getattr(bot, "name", bot.__class__.__name__)
    LOGGER.debug(
        "telegram send attempt kind=service bot=%s chat_id=%s thread_id=%s text=%s",
        bot_name,
        getattr(bot, "chat_id", ""),
        getattr(bot, "message_thread_id", None),
        preview_text(rendered),
    )
    try:
        bot.send_message(rendered)
    except TelegramError as exc:
        LOGGER.error("telegram send failed kind=service bot=%s error=%s", bot_name, exc)
        raise MulticodersError(str(exc)) from exc
    LOGGER.debug("telegram send ok kind=service bot=%s", bot_name)


RICH_DIRECTIVE_RE = re.compile(r"^\s*\[(gif|meme|sticker|emoji):\s*([^\]]+?)\s*\]\s*$", flags=re.IGNORECASE)


def parse_rich_chat_response(text: str, catalogs: dict[str, dict[str, str]] | None = None) -> RichChatResponse:
    active_catalogs = catalogs if catalogs is not None else media_catalogs()
    text_lines: list[str] = []
    media: list[RichMediaRequest] = []
    caption_source = ""
    for raw_line in text.splitlines():
        match = RICH_DIRECTIVE_RE.match(raw_line)
        if match is None:
            text_lines.append(raw_line)
            if raw_line.strip():
                caption_source = raw_line.strip()
            continue
        kind = match.group(1).lower()
        value = match.group(2).strip()
        if kind == "emoji":
            if value:
                text_lines.append(value)
                if not caption_source:
                    caption_source = value
            continue
        key = value.lower()
        asset = active_catalogs.get(kind, {}).get(key)
        if asset is None:
            text_lines.append(f"[{kind}:{value}]")
            caption_source = f"[{kind}:{value}]"
            continue
        media.append(RichMediaRequest(kind=kind, key=key, asset=asset, caption=caption_source or None))
    return RichChatResponse(text=trim_chat_output("\n".join(text_lines)), media=media)


def send_group_media(agent: AgentConfig, media: RichMediaRequest, dry_run: bool, run_id: str | None = None) -> None:
    caption = redact_sensitive_text(annotate_message(media.caption, run_id)) if media.caption else None
    if dry_run:
        LOGGER.debug(
            "telegram media send skipped mode=dry-run provider=%s kind=%s key=%s caption=%s",
            agent.provider,
            media.kind,
            media.key,
            preview_text(caption or ""),
        )
        return
    if agent.bot is None:
        LOGGER.debug("telegram media send skipped mode=no-bot provider=%s kind=%s key=%s", agent.provider, media.kind, media.key)
        return
    bot_name = getattr(agent.bot, "name", agent.display_name)
    LOGGER.debug("telegram media send attempt provider=%s bot=%s kind=%s key=%s", agent.provider, bot_name, media.kind, media.key)
    try:
        if media.kind == "gif":
            agent.bot.send_animation(media.asset, caption=caption)
        elif media.kind == "meme":
            agent.bot.send_photo(media.asset, caption=caption)
        elif media.kind == "sticker":
            agent.bot.send_sticker(media.asset)
        else:
            raise MulticodersError(f"unsupported rich media kind: {media.kind}")
    except TelegramError as exc:
        LOGGER.error("telegram media send failed provider=%s bot=%s kind=%s key=%s error=%s", agent.provider, bot_name, media.kind, media.key, exc)
        raise MulticodersError(str(exc)) from exc
    LOGGER.debug("telegram media send ok provider=%s bot=%s kind=%s key=%s", agent.provider, bot_name, media.kind, media.key)


def send_chat_response(agent: AgentConfig, text: str, dry_run: bool, run_id: str | None = None) -> RichChatResponse:
    response = parse_rich_chat_response(text)
    if response.text:
        send_group_message(agent, response.text, dry_run, run_id=run_id)
    elif not response.media:
        send_group_message(agent, text, dry_run, run_id=run_id)
    for media in response.media:
        send_group_media(agent, media, dry_run, run_id=run_id)
    return response


def brainstorm_session_active(state: TelegramState) -> dict[str, object] | None:
    session = state.brainstorming
    if isinstance(session, dict) and session.get("active"):
        return session
    return None


def brainstorm_proposal_ids(session: dict[str, object]) -> list[str]:
    proposals = session.get("proposals")
    if not isinstance(proposals, list):
        return []
    ids: list[str] = []
    for item in proposals:
        if isinstance(item, dict):
            proposal_id = item.get("proposal_id")
            if isinstance(proposal_id, str) and proposal_id:
                ids.append(proposal_id)
    return ids


def brainstorm_current_round(session: dict[str, object]) -> int:
    value = session.get("round_number", 1)
    return int(value) if isinstance(value, int) or str(value).isdigit() else 1


def brainstorm_stage(session: dict[str, object]) -> str:
    value = session.get("stage", "proposal")
    return str(value) if isinstance(value, str) else "proposal"


def brainstorm_transcript(session: dict[str, object]) -> list[dict[str, str]]:
    raw = session.get("transcript")
    if not isinstance(raw, list):
        return []
    transcript: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, dict):
            speaker = item.get("speaker")
            text = item.get("text")
            if isinstance(speaker, str) and isinstance(text, str) and speaker and text:
                transcript.append({"speaker": speaker, "text": text})
    return transcript


def store_brainstorm_transcript(session: dict[str, object], transcript: list[dict[str, str]]) -> None:
    session["transcript"] = transcript[-40:]


def append_brainstorm_round_snapshot(session: dict[str, object]) -> None:
    rounds = list(session.get("proposal_rounds") or [])
    rounds.append(
        {
            "round_number": session.get("round_number", 1),
            "stage": session.get("stage", ""),
            "proposals": session.get("current_proposals", []),
            "proposal_ranking": session.get("proposal_ranking", []),
            "improvements": session.get("current_improvements", []),
            "improvement_ranking": session.get("improvement_ranking", []),
            "selected_proposal_id": session.get("selected_proposal_id", ""),
            "selected_improvement_id": session.get("selected_improvement_id", ""),
        }
    )
    session["proposal_rounds"] = rounds[-12:]


def brainstorm_output_path(repo: Path, topic: str, run_id: str) -> Path:
    return brainstorming_docs_dir(repo) / f"{slugify_text(topic)}-{run_id}.md"


def resolve_brainstorm_output_path(session: dict[str, object], repo: Path) -> Path:
    docs_dir = session.get("docs_dir")
    topic = str(session.get("topic") or "")
    run_id = str(session.get("run_id") or "brain")
    if isinstance(docs_dir, str) and docs_dir.strip():
        base_dir = Path(docs_dir).expanduser()
        if not base_dir.is_absolute():
            base_dir = repo / base_dir
        return base_dir / f"{slugify_text(topic)}-{run_id}.md"
    return brainstorm_output_path(repo, topic, run_id)


def brainstorm_summary_text(session: dict[str, object]) -> str:
    topic = str(session.get("topic") or "")
    stage = brainstorm_stage(session)
    round_number = brainstorm_current_round(session)
    proposal_count = len(brainstorm_proposal_ids(session))
    return f"topic={topic} stage={stage} round={round_number} proposals={proposal_count}"


def brainstorm_markdown_summary(session: dict[str, object]) -> str:
    topic = str(session.get("topic") or "brainstorming")
    run_id = str(session.get("run_id") or "")
    chosen = str(session.get("chosen_solution_id") or "")
    chosen_improvement = str(session.get("chosen_improvement_id") or "")
    lines = [
        f"# Brainstorming: {topic}",
        "",
        f"- run_id: {run_id}",
        f"- stage: {brainstorm_stage(session)}",
        f"- round: {brainstorm_current_round(session)}",
        f"- chosen_solution_id: {chosen or 'pending'}",
        f"- chosen_improvement_id: {chosen_improvement or 'pending'}",
        "",
        "## Transcript",
    ]
    for item in brainstorm_transcript(session):
        lines.append(f"- {item['speaker']}: {item['text']}")
    return "\n".join(lines).strip() + "\n"


def brainstorm_peer_scores(
    proposals: list[dict[str, object]],
    score_payloads: list[dict[str, object]],
) -> dict[str, dict[str, int]]:
    author_by_proposal = {
        str(item.get("proposal_id")): str(item.get("author") or "")
        for item in proposals
        if isinstance(item, dict) and isinstance(item.get("proposal_id"), str)
    }
    scores_by_proposal: dict[str, dict[str, int]] = {}
    for payload in score_payloads:
        scorer = str(payload.get("scorer") or "")
        scores = payload.get("scores")
        if not isinstance(scores, dict):
            continue
        for proposal_id, raw_score in scores.items():
            if proposal_id not in author_by_proposal:
                continue
            try:
                score_value = int(raw_score)
            except (TypeError, ValueError):
                continue
            if score_value < 1 or score_value > 10:
                continue
            if author_by_proposal[proposal_id] == scorer:
                continue
            scores_by_proposal.setdefault(proposal_id, {})[scorer] = score_value
    return scores_by_proposal


def brainstorm_aggregate_proposal_scores(
    proposals: list[dict[str, object]],
    score_payloads: list[dict[str, object]],
) -> list[dict[str, object]]:
    peer_scores = brainstorm_peer_scores(proposals, score_payloads)
    ranking: list[dict[str, object]] = []
    for proposal in proposals:
        proposal_id = str(proposal.get("proposal_id") or "")
        if not proposal_id:
            continue
        self_score = int(proposal.get("self_score") or 0)
        peers = peer_scores.get(proposal_id, {})
        peer_average = round(sum(peers.values()) / len(peers), 2) if peers else 0.0
        aggregate = round((self_score * 0.4) + (peer_average * 0.6), 2)
        ranking.append(
            {
                "proposal_id": proposal_id,
                "author": proposal.get("author", ""),
                "summary": proposal.get("summary", ""),
                "self_score": self_score,
                "peer_scores": peers,
                "peer_average": peer_average,
                "aggregate_score": aggregate,
            }
        )
    ranking.sort(key=lambda item: (item["aggregate_score"], item["peer_average"], item["self_score"], item["proposal_id"]), reverse=True)
    return ranking


def brainstorm_aggregate_improvement_scores(
    improvements: list[dict[str, object]],
    score_payloads: list[dict[str, object]],
) -> list[dict[str, object]]:
    author_by_id = {
        str(item.get("improvement_id")): str(item.get("author") or "")
        for item in improvements
        if isinstance(item, dict) and isinstance(item.get("improvement_id"), str)
    }
    scores_by_id: dict[str, dict[str, int]] = {}
    for payload in score_payloads:
        scorer = str(payload.get("scorer") or "")
        scores = payload.get("scores")
        if not isinstance(scores, dict):
            continue
        for improvement_id, raw_score in scores.items():
            if improvement_id not in author_by_id:
                continue
            try:
                score_value = int(raw_score)
            except (TypeError, ValueError):
                continue
            if score_value < 1 or score_value > 10:
                continue
            if author_by_id[improvement_id] == scorer:
                continue
            scores_by_id.setdefault(improvement_id, {})[scorer] = score_value
    ranking: list[dict[str, object]] = []
    for improvement in improvements:
        improvement_id = str(improvement.get("improvement_id") or "")
        if not improvement_id:
            continue
        self_score = int(improvement.get("self_score") or 0)
        peers = scores_by_id.get(improvement_id, {})
        peer_average = round(sum(peers.values()) / len(peers), 2) if peers else 0.0
        aggregate = round((self_score * 0.4) + (peer_average * 0.6), 2)
        ranking.append(
            {
                "improvement_id": improvement_id,
                "author": improvement.get("author", ""),
                "summary": improvement.get("summary", ""),
                "self_score": self_score,
                "peer_scores": peers,
                "peer_average": peer_average,
                "aggregate_score": aggregate,
            }
        )
    ranking.sort(key=lambda item: (item["aggregate_score"], item["peer_average"], item["self_score"], item["improvement_id"]), reverse=True)
    return ranking


def brainstorm_unanimous_choice(payloads: list[dict[str, object]], field: str) -> str | None:
    votes = [str(item.get(field) or "") for item in payloads if isinstance(item, dict)]
    votes = [vote for vote in votes if vote]
    if not votes:
        return None
    first = votes[0]
    if all(vote == first for vote in votes):
        return first
    return None


def brainstorm_next_stage(session: dict[str, object], stage: str) -> None:
    session["stage"] = stage
    session["last_turn_at"] = utc_now_iso()


def format_brainstorm_message(stage: str, payload: dict[str, object], aggregate: list[dict[str, object]] | None = None) -> str:
    lines = [f"[brainstorm:{stage}] {payload.get('solution_id', '')}"]
    summary = payload.get("summary")
    if isinstance(summary, str) and summary.strip():
        lines.append(f"summary: {summary.strip()}")
    if stage == "proposal":
        if isinstance(payload.get("approach"), str):
            lines.append(f"approach: {payload['approach']}")
        if isinstance(payload.get("self_score"), int):
            lines.append(f"self_score: {payload['self_score']}")
        for key in ("risks", "improvement_ideas"):
            value = payload.get(key)
            if isinstance(value, list) and value:
                lines.append(f"{key}:")
                for item in value[:5]:
                    lines.append(f"- {item}")
    elif stage in {"score", "improvement_score"} and aggregate is not None:
        best = aggregate[0] if aggregate else None
        if best:
            lines.append(f"best: {best['proposal_id' if stage == 'score' else 'improvement_id']}")
            lines.append(f"aggregate_score: {best['aggregate_score']}")
        for item in aggregate[:3]:
            item_id = item["proposal_id" if stage == "score" else "improvement_id"]
            lines.append(f"- {item_id}: {item['aggregate_score']} (self {item['self_score']}, peers {item['peer_average']})")
    elif stage in {"score", "improvement_score"}:
        scores = payload.get("scores")
        if isinstance(scores, dict):
            lines.append("scores:")
            for key, value in list(scores.items())[:6]:
                lines.append(f"- {key}: {value}")
        best_key = "best_proposal_id"
        if isinstance(payload.get(best_key), str):
            lines.append(f"best: {payload[best_key]}")
    elif stage == "improvement":
        if isinstance(payload.get("target_solution_id"), str):
            lines.append(f"target_solution_id: {payload['target_solution_id']}")
        if isinstance(payload.get("improvement"), str):
            lines.append(f"improvement: {payload['improvement']}")
        if isinstance(payload.get("self_score"), int):
            lines.append(f"self_score: {payload['self_score']}")
        value = payload.get("tradeoffs")
        if isinstance(value, list) and value:
            lines.append("tradeoffs:")
            for item in value[:5]:
                lines.append(f"- {item}")
    elif stage in {"proposal_vote", "improvement_vote"} and isinstance(payload.get("vote_for"), str):
        lines.append(f"vote_for: {payload['vote_for']}")
    elif stage == "spec":
        if isinstance(payload.get("spec_title"), str):
            lines.append(f"spec_title: {payload['spec_title']}")
    return "\n".join(lines)

def format_service_event(
    *,
    event: str,
    task_id: int | None = None,
    repo_path: str | None = None,
    task_type: str | None = None,
    task_text: str | None = None,
    lead_provider: str | None = None,
    status: str | None = None,
    winner: str | None = None,
    details: str | None = None,
) -> str:
    opening = {
        "pending": "[multicoders-service] Recibí una tarea nueva.",
        "approved": "[multicoders-service] La tarea fue aprobada.",
        "rejected": "[multicoders-service] La tarea fue rechazada.",
        "retried": "[multicoders-service] La tarea volvió a la cola.",
        "running": "[multicoders-service] Estamos trabajando en la tarea.",
        "done": "[multicoders-service] La tarea terminó.",
        "failed": "[multicoders-service] La tarea falló.",
    }.get(event, f"[multicoders-service] event={event}")
    lines = [opening]
    if task_id is not None:
        lines.append(f"task_id: {task_id}")
    if status:
        lines.append(f"status: {status}")
    if repo_path:
        lines.append(f"repo: {repo_path}")
    if task_type:
        lines.append(f"type: {task_type}")
    if lead_provider:
        lines.append(f"lead: {lead_provider}")
    if task_text:
        lines.append(f"text: {task_text}")
    if winner:
        lines.append(f"winner: {winner}")
    if details:
        lines.append(f"details: {details}")
    return "\n".join(lines)


def trim_chat_output(text: str) -> str:
    lines = [line.rstrip() for line in text.strip().splitlines()]
    compact: list[str] = []
    previous_blank = False
    for line in lines:
        blank = not line.strip()
        if blank and previous_blank:
            continue
        compact.append(line)
        previous_blank = blank
    return "\n".join(compact).strip()


def _run_provider_with_optional_cooldown(**kwargs) -> ProviderResult:
    try:
        return run_provider(**kwargs)
    except TypeError as exc:
        if "cooldown_sec" not in str(exc):
            raise
        retry_kwargs = dict(kwargs)
        retry_kwargs.pop("cooldown_sec", None)
        return run_provider(**retry_kwargs)


def agent_internal_log_path(repo: Path, provider: str, run_id: str | None = None) -> Path:
    safe_provider = re.sub(r"[^a-zA-Z0-9_.-]+", "_", provider).strip("._") or "agent"
    if run_id:
        return repo / ".multicoders" / "agent-logs" / safe_provider / f"{run_id}.jsonl"
    return repo / ".multicoders" / "agent-logs" / f"{safe_provider}.jsonl"


def redact_agent_internal_log_text(text: str) -> str:
    return redact_sensitive_text(text)


def agent_internal_log_max_bytes() -> int:
    raw = os.environ.get("MULTICODERS_AGENT_LOG_MAX_BYTES", "")
    if not raw:
        return DEFAULT_AGENT_INTERNAL_LOG_MAX_BYTES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_AGENT_INTERNAL_LOG_MAX_BYTES
    return max(value, 0)


def rotate_agent_internal_log(path: Path, *, max_bytes: int | None = None) -> None:
    limit = agent_internal_log_max_bytes() if max_bytes is None else max_bytes
    if limit <= 0 or not path.exists():
        return
    try:
        if path.stat().st_size < limit:
            return
        rotated = path.with_name(path.name + ".1")
        if rotated.exists():
            rotated.unlink()
        path.replace(rotated)
    except OSError as exc:
        LOGGER.warning("agent internal log rotation failed path=%s error=%s", path, exc)


def write_agent_internal_log(
    *,
    repo: Path,
    agent: AgentConfig,
    run_id: str,
    result: ProviderResult,
    phase: str,
) -> None:
    path = agent_internal_log_path(repo, agent.provider, run_id=run_id)
    payload = {
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "run_id": run_id,
        "phase": phase,
        "provider": agent.provider,
        "display_name": agent.display_name,
        "model": agent.model,
        "stdout": redact_agent_internal_log_text(result.stdout),
        "stderr": redact_agent_internal_log_text(result.stderr),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        rotate_agent_internal_log(path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError as exc:
        LOGGER.warning("agent internal log write failed provider=%s path=%s error=%s", agent.provider, path, exc)


def ask_chat_agent(
    *,
    agent: AgentConfig,
    sender_name: str,
    user_message: str,
    prior_messages: list[dict[str, str]],
    repo: Path,
    timeout_sec: int,
    dry_run: bool,
    run_id: str,
    catchup_note: str | None = None,
) -> str:
    prompt = build_chat_prompt(
        provider_name=agent.provider,
        sender_name=sender_name,
        user_message=user_message,
        prior_messages=prior_messages,
        media_capabilities=format_chat_media_capabilities(),
        catchup_note=catchup_note,
    )
    LOGGER.info("chat turn started run_id=%s provider=%s", run_id, agent.provider)
    if dry_run:
        answer = f"Soy {agent.provider} y agrego un matiz sobre: {user_message}"
        if prior_messages:
            answer += f" Tomando lo anterior, sumo algo sobre {prior_messages[-1]['speaker']}."
    else:
        effective_timeout_sec = min(timeout_sec, CHAT_PROVIDER_TIMEOUT_SEC)
        result = _run_provider_with_optional_cooldown(
            provider_name=agent.provider,
            prompt=prompt,
            repo=repo,
            model=agent.model,
            timeout_sec=effective_timeout_sec,
            cooldown_sec=PROVIDER_COOLDOWN_SEC,
        )
        write_agent_internal_log(repo=repo, agent=agent, run_id=run_id, result=result, phase="chat")
        answer = trim_chat_output(result.text_output())
        if not answer:
            log_path = agent_internal_log_path(repo, agent.provider, run_id=run_id)
            raise ProviderError(
                f"{agent.provider} returned no human response; see {log_path}",
                summary=f"{agent.provider} no devolvió respuesta humana; revisar agent-logs",
                raw_stdout=result.stdout,
                raw_stderr=result.stderr,
            )
    LOGGER.info("chat turn finished run_id=%s provider=%s", run_id, agent.provider)
    send_chat_response(agent, answer, dry_run, run_id=run_id)
    return answer


def run_group_conversation(
    *,
    agents: list[AgentConfig],
    sender_name: str,
    user_message: str,
    repo: Path,
    timeout_sec: int,
    dry_run: bool,
    observer_bot: TelegramBot | None,
    run_id: str | None = None,
    initial_transcript: list[dict[str, str]] | None = None,
    state: TelegramState | None = None,
    conn=None,
) -> list[dict[str, str]]:
    active_run_id = run_id or f"chat-{uuid.uuid4().hex[:10]}"
    send_service_message(
        observer_bot,
        f"[multicoders] {sender_name} preguntó: {user_message}",
        dry_run,
        run_id=active_run_id,
    )
    transcript = list(initial_transcript or [])
    initial_length = len(transcript)
    for agent in agents:
        catchup_note = None
        if state is not None:
            catchup_note = handle_provider_wake(
                state=state,
                agent=agent,
                observer_bot=observer_bot,
                dry_run=dry_run,
                run_id=active_run_id,
                conn=conn,
            )
            if is_provider_sleeping(state, agent.provider):
                record_pending_for_provider(
                    state,
                    provider=agent.provider,
                    sender_name=sender_name,
                    user_message=user_message,
                    transcript_tail=transcript,
                )
                save_telegram_state(state)
                LOGGER.info(
                    "chat turn skipped run_id=%s provider=%s reason=sleeping",
                    active_run_id,
                    agent.provider,
                )
                continue
        try:
            answer = ask_chat_agent(
                agent=agent,
                sender_name=sender_name,
                user_message=user_message,
                prior_messages=transcript,
                repo=repo,
                timeout_sec=timeout_sec,
                dry_run=dry_run,
                run_id=active_run_id,
                catchup_note=catchup_note,
            )
        except ProviderQuotaError as exc:
            handle_provider_quota_failure(
                state=state,
                agent=agent,
                exc=exc,
                sender_name=sender_name,
                user_message=user_message,
                transcript_tail=transcript,
                observer_bot=observer_bot,
                dry_run=dry_run,
                run_id=active_run_id,
            )
            continue
        except Exception as exc:
            LOGGER.warning("chat agent failed run_id=%s provider=%s error=%s", active_run_id, agent.provider, exc)
            log_exception_details(exc)
            send_service_message(
                observer_bot,
                f"[multicoders-service] {agent.display_name} no pudo responder.\ndetails: {short_exception_text(exc)}",
                dry_run,
                run_id=active_run_id,
            )
            continue
        transcript.append({"speaker": agent.display_name, "text": answer})
    if len(transcript) == initial_length:
        raise MulticodersError("ningún bot pudo responder al mensaje")
    return transcript


def run_single_human_response(
    *,
    agents: list[AgentConfig],
    sender_name: str,
    user_message: str,
    repo: Path,
    timeout_sec: int,
    dry_run: bool,
    observer_bot: TelegramBot | None,
    state: TelegramState | None = None,
    conn=None,
) -> list[dict[str, str]]:
    agent = first_available_agent(agents, state)
    if agent is None:
        raise MulticodersError("no hay bots configurados para responder")
    run_id = f"chat-{uuid.uuid4().hex[:10]}"
    previous_chat = active_bot_chat(state) if state is not None else None
    transcript = transcript_from_state(previous_chat) if previous_chat is not None else []
    catchup_note = None
    if state is not None:
        catchup_note = handle_provider_wake(
            state=state,
            agent=agent,
            observer_bot=observer_bot,
            dry_run=dry_run,
            run_id=run_id,
            conn=conn,
        )
        if is_provider_sleeping(state, agent.provider):
            record_pending_for_provider(
                state,
                provider=agent.provider,
                sender_name=sender_name,
                user_message=user_message,
                transcript_tail=transcript,
            )
            save_telegram_state(state)
            raise MulticodersError(f"{agent.display_name} no está disponible para responder")
    answer = ask_chat_agent(
        agent=agent,
        sender_name=sender_name,
        user_message=user_message,
        prior_messages=transcript,
        repo=repo,
        timeout_sec=timeout_sec,
        dry_run=dry_run,
        run_id=run_id,
        catchup_note=catchup_note,
    )
    transcript.append({"speaker": agent.display_name, "text": answer})
    if state is not None:
        state.bot_chat = {
            "active": False,
            "run_id": run_id,
            "sender_name": sender_name,
            "seed_message": user_message,
            "next_provider_index": 0,
            "started_at": utc_now_iso(),
            "last_turn_at": utc_now_iso(),
            "transcript": transcript[-AUTONOMOUS_CHAT_TRANSCRIPT_LIMIT:],
        }
    return transcript


def active_bot_chat(state: TelegramState) -> dict[str, object] | None:
    chat = state.bot_chat
    if isinstance(chat, dict) and chat.get("active"):
        return chat
    return None


def transcript_from_state(chat: dict[str, object]) -> list[dict[str, str]]:
    raw_transcript = chat.get("transcript")
    if not isinstance(raw_transcript, list):
        return []
    transcript: list[dict[str, str]] = []
    for item in raw_transcript:
        if not isinstance(item, dict):
            continue
        speaker = item.get("speaker")
        text = item.get("text")
        if isinstance(speaker, str) and isinstance(text, str) and speaker and text:
            transcript.append({"speaker": speaker, "text": text})
    return transcript


def store_bot_chat_transcript(chat: dict[str, object], transcript: list[dict[str, str]]) -> None:
    chat["transcript"] = transcript[-AUTONOMOUS_CHAT_TRANSCRIPT_LIMIT:]


def start_autonomous_bot_chat(
    *,
    state: TelegramState,
    agents: list[AgentConfig],
    sender_name: str,
    user_message: str,
    repo: Path,
    timeout_sec: int,
    dry_run: bool,
    observer_bot: TelegramBot | None,
    conn=None,
) -> None:
    run_id = f"chat-{uuid.uuid4().hex[:10]}"
    previous_chat = active_bot_chat(state)
    initial_transcript = transcript_from_state(previous_chat) if previous_chat is not None else []
    transcript = run_group_conversation(
        agents=agents,
        sender_name=sender_name,
        user_message=user_message,
        repo=repo,
        timeout_sec=timeout_sec,
        dry_run=dry_run,
        observer_bot=observer_bot,
        run_id=run_id,
        initial_transcript=initial_transcript,
        state=state,
        conn=conn,
    )
    state.bot_chat = {
        "active": True,
        "run_id": run_id,
        "sender_name": sender_name,
        "seed_message": user_message,
        "next_provider_index": 0,
        "started_at": utc_now_iso(),
        "last_turn_at": utc_now_iso(),
        "transcript": transcript[-AUTONOMOUS_CHAT_TRANSCRIPT_LIMIT:],
    }


def stop_autonomous_bot_chat(
    *,
    state: TelegramState,
    observer_bot: TelegramBot | None,
    dry_run: bool,
    requested_by: str,
) -> None:
    chat = active_bot_chat(state)
    if chat is None:
        send_service_message(
            observer_bot,
            "[multicoders] silencio recibido. No hay conversación autónoma activa.",
            dry_run,
        )
        return
    run_id = str(chat.get("run_id") or "")
    state.bot_chat = None
    send_service_message(
        observer_bot,
        f"[multicoders] silencio recibido por {requested_by}. Corto la conversación autónoma.",
        dry_run,
        run_id=run_id or None,
    )


def advance_autonomous_bot_chat(
    *,
    state: TelegramState,
    agents: list[AgentConfig],
    repo: Path,
    timeout_sec: int,
    dry_run: bool,
    observer_bot: TelegramBot | None = None,
    conn=None,
) -> None:
    chat = active_bot_chat(state)
    if chat is None or not agents:
        return
    transcript = transcript_from_state(chat)
    next_index = int(chat.get("next_provider_index", 0) or 0) % len(agents)
    run_id = str(chat.get("run_id") or f"chat-{uuid.uuid4().hex[:10]}")
    sender_name = str(chat.get("sender_name") or "grupo")
    user_message = str(chat.get("seed_message") or "Continuar la conversación.")
    failures: list[str] = []
    for attempt in range(len(agents)):
        agent_index = (next_index + attempt) % len(agents)
        agent = agents[agent_index]
        catchup_note = handle_provider_wake(
            state=state,
            agent=agent,
            observer_bot=observer_bot,
            dry_run=dry_run,
            run_id=run_id,
            conn=conn,
        )
        if is_provider_sleeping(state, agent.provider):
            record_pending_for_provider(
                state,
                provider=agent.provider,
                sender_name=sender_name,
                user_message=user_message,
                transcript_tail=transcript,
            )
            save_telegram_state(state)
            failures.append(f"{agent.display_name}: durmiendo")
            continue
        try:
            answer = ask_chat_agent(
                agent=agent,
                sender_name=sender_name,
                user_message=user_message,
                prior_messages=transcript,
                repo=repo,
                timeout_sec=timeout_sec,
                dry_run=dry_run,
                run_id=run_id,
                catchup_note=catchup_note,
            )
        except ProviderQuotaError as exc:
            handle_provider_quota_failure(
                state=state,
                agent=agent,
                exc=exc,
                sender_name=sender_name,
                user_message=user_message,
                transcript_tail=transcript,
                observer_bot=observer_bot,
                dry_run=dry_run,
                run_id=run_id,
            )
            failures.append(f"{agent.display_name}: sin tokens")
            continue
        except Exception as exc:
            short_detail = short_exception_text(exc)
            failures.append(f"{agent.display_name}: {short_detail}")
            LOGGER.warning("autonomous chat agent failed run_id=%s provider=%s error=%s", run_id, agent.provider, exc)
            log_exception_details(exc)
            send_service_message(
                observer_bot,
                f"[multicoders-service] {agent.display_name} no pudo responder.\ndetails: {short_detail}",
                dry_run,
                run_id=run_id,
            )
            continue
        transcript.append({"speaker": agent.display_name, "text": answer})
        store_bot_chat_transcript(chat, transcript)
        chat["next_provider_index"] = (agent_index + 1) % len(agents)
        chat["last_turn_at"] = utc_now_iso()
        chat["run_id"] = run_id
        state.bot_chat = chat
        return
    raise MulticodersError("ningún bot pudo avanzar la conversación: " + "; ".join(failures))


def ask_agent(
    agent: AgentConfig,
    phase: str,
    context: RepoContext,
    task_type: str,
    task: str,
    prior_payloads: list[dict[str, object]],
    timeout_sec: int,
    dry_run: bool,
    winning_solution_id: str | None = None,
    candidate_solution_ids: list[str] | None = None,
    run_id: str | None = None,
) -> dict[str, object]:
    prompt = build_discussion_prompt(
        provider_name=agent.provider,
        phase=phase,
        repo=context.repo,
        task_type=task_type,
        task=task,
        stack=context.stack,
        branch=context.branch,
        recent_commit=context.recent_commit,
        candidate_files=context.candidate_files,
        prior_payloads=prior_payloads,
        winning_solution_id=winning_solution_id,
        candidate_solution_ids=candidate_solution_ids,
    )
    LOGGER.info("agent phase started run_id=%s phase=%s provider=%s", run_id or "", phase, agent.provider)
    if dry_run:
        payload = build_dry_run_payload(
            agent=agent,
            phase=phase,
            candidate_solution_ids=candidate_solution_ids or [],
            winning_solution_id=winning_solution_id,
        )
    else:
        result = run_provider(
            provider_name=agent.provider,
            prompt=prompt,
            repo=context.repo,
            model=agent.model,
            timeout_sec=timeout_sec,
        )
        payload = result.parse_json()
        if not phase_payload_is_valid(phase, payload):
            LOGGER.warning("agent phase invalid payload run_id=%s phase=%s provider=%s attempting autocorrect", run_id or "", phase, agent.provider)
            invalid_summary = {
                key: payload.get(key)
                for key in (
                    "solution_id",
                    "summary",
                    "preferred_solution_id",
                    "vote_for",
                    "implemented_solution_id",
                    "response",
                    "content",
                    "text",
                    "result",
                )
                if key in payload
            }
            correction_prompt = (
                "Return only corrected JSON for the same task and phase. "
                f"Missing or invalid required fields for phase '{phase}'. "
                f"Previous payload summary was:\n{json.dumps(invalid_summary, ensure_ascii=False)[:4000]}"
            )
            correction_result = run_provider(
                provider_name=agent.provider,
                prompt=correction_prompt,
                repo=context.repo,
                model=agent.model,
                timeout_sec=min(timeout_sec, 120),
            )
            corrected_payload = correction_result.parse_json()
            if phase_payload_is_valid(phase, corrected_payload):
                payload = corrected_payload
    LOGGER.info(
        "agent phase finished run_id=%s phase=%s provider=%s solution_id=%s vote_for=%s",
        run_id or "",
        phase,
        agent.provider,
        payload.get("solution_id", ""),
        payload.get("vote_for", ""),
    )

    message = format_group_message(agent.display_name, phase, payload)
    send_group_message(agent, message, dry_run, run_id=run_id)
    return payload


def ask_brainstorm_agent(
    agent: AgentConfig,
    phase: str,
    context: RepoContext,
    topic: str,
    prior_payloads: list[dict[str, object]],
    round_number: int,
    timeout_sec: int,
    dry_run: bool,
    run_id: str,
    selected_solution_id: str | None = None,
    selected_improvement_id: str | None = None,
) -> dict[str, object]:
    prompt = build_brainstorm_prompt(
        provider_name=agent.provider,
        phase=phase,
        topic=topic,
        repo=context.repo,
        stack=context.stack,
        branch=context.branch,
        recent_commit=context.recent_commit,
        candidate_files=context.candidate_files,
        prior_payloads=prior_payloads,
        round_number=round_number,
        selected_solution_id=selected_solution_id,
        selected_improvement_id=selected_improvement_id,
        media_capabilities=format_chat_media_capabilities(),
    )
    LOGGER.info("brainstorm phase started run_id=%s phase=%s round=%s provider=%s", run_id, phase, round_number, agent.provider)
    if dry_run:
        payload = build_dry_run_brainstorm_payload(
            agent=agent,
            phase=phase,
            topic=topic,
            round_number=round_number,
            prior_payloads=prior_payloads,
            selected_solution_id=selected_solution_id,
            selected_improvement_id=selected_improvement_id,
        )
    else:
        result = run_provider(
            provider_name=agent.provider,
            prompt=prompt,
            repo=context.repo,
            model=agent.model,
            timeout_sec=timeout_sec,
        )
        payload = result.parse_json()
    if not phase_payload_is_valid(phase, payload):
        raise MulticodersError(f"invalid brainstorming payload for phase {phase} from {agent.provider}")
    LOGGER.info(
        "brainstorm phase finished run_id=%s phase=%s round=%s provider=%s solution_id=%s",
        run_id,
        phase,
        round_number,
        agent.provider,
        payload.get("solution_id", ""),
    )
    send_group_message(agent, format_brainstorm_message(phase, payload), dry_run, run_id=run_id)
    return payload


def build_dry_run_brainstorm_payload(
    *,
    agent: AgentConfig,
    phase: str,
    topic: str,
    round_number: int,
    prior_payloads: list[dict[str, object]],
    selected_solution_id: str | None,
    selected_improvement_id: str | None,
) -> dict[str, object]:
    base_id = f"{agent.provider}-brainstorm-r{round_number}"
    proposal_ids = [
        str(item.get("proposal_id") or item.get("improvement_id") or item.get("solution_id") or f"{agent.provider}-proposal-{index}")
        for index, item in enumerate(prior_payloads, start=1)
        if isinstance(item, dict)
    ]
    if phase == "proposal":
        return {
            "solution_id": f"{base_id}-proposal",
            "summary": f"dry-run proposal from {agent.provider} about {topic}",
            "approach": f"{agent.provider} focuses on a staged solution",
            "self_score": 7,
            "risks": ["dry-run placeholder"],
            "improvement_ideas": ["tighten interfaces", "add validation"],
        }
    if phase == "score":
        scores = {proposal_id: 7 for proposal_id in proposal_ids}
        return {
            "solution_id": f"{base_id}-score",
            "scores": scores,
            "best_proposal_id": proposal_ids[0] if proposal_ids else f"{base_id}-proposal",
            "summary": f"dry-run scoring from {agent.provider}",
            "reasons": ["balanced tradeoffs", "clear validation path"],
        }
    if phase == "improvement":
        return {
            "solution_id": f"{base_id}-improvement",
            "target_solution_id": selected_solution_id or f"{base_id}-proposal",
            "improvement_id": f"{base_id}-improvement-1",
            "summary": f"dry-run improvement from {agent.provider}",
            "improvement": "add stronger guardrails and observability",
            "self_score": 8,
            "tradeoffs": ["more work", "better safety"],
        }
    if phase in {"proposal_vote", "improvement_vote"}:
        return {
            "solution_id": f"{base_id}-{phase.replace('_', '-')}",
            "vote_for": selected_improvement_id or selected_solution_id or f"{base_id}-improvement-1",
            "summary": f"dry-run {phase} from {agent.provider}",
            "reasoning": ["most practical", "best improvement"],
        }
    if phase == "spec":
        title = f"Spec for {topic}"
        return {
            "solution_id": f"{base_id}-spec",
            "spec_title": title,
            "file_name": f"{slugify_text(topic)}.md",
            "spec_markdown": f"# {title}\n\n- dry-run only\n- provider: {agent.provider}\n",
            "summary": f"dry-run spec from {agent.provider}",
        }
    return {
        "solution_id": f"{base_id}-{phase}",
        "summary": f"dry-run response from {agent.provider}",
    }


def phase_payload_is_valid(phase: str, payload: dict[str, object]) -> bool:
    if phase == "spec":
        return _non_empty_string(payload.get("solution_id")) and _non_empty_string(payload.get("summary"))
    if phase == "review":
        return _non_empty_string(payload.get("solution_id")) and _non_empty_string(payload.get("preferred_solution_id"))
    if phase in {"vote", "tie_break"}:
        return _non_empty_string(payload.get("solution_id")) and _non_empty_string(payload.get("vote_for"))
    if phase == "implement":
        return _non_empty_string(payload.get("solution_id")) and _non_empty_string(payload.get("implemented_solution_id"))
    if phase == "proposal":
        self_score = payload.get("self_score")
        return _non_empty_string(payload.get("solution_id")) and _non_empty_string(payload.get("summary")) and isinstance(self_score, int) and 1 <= self_score <= 10
    if phase == "score":
        scores = payload.get("scores")
        return _non_empty_string(payload.get("solution_id")) and isinstance(scores, dict) and _non_empty_string(payload.get("best_proposal_id"))
    if phase == "improvement":
        self_score = payload.get("self_score")
        return _non_empty_string(payload.get("solution_id")) and _non_empty_string(payload.get("target_solution_id")) and _non_empty_string(payload.get("improvement_id")) and isinstance(self_score, int) and 1 <= self_score <= 10
    if phase in {"proposal_vote", "improvement_vote"}:
        return _non_empty_string(payload.get("solution_id")) and _non_empty_string(payload.get("vote_for"))
    return _non_empty_string(payload.get("solution_id")) or _non_empty_string(payload.get("summary"))


def _non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def build_dry_run_payload(
    *,
    agent: AgentConfig,
    phase: str,
    candidate_solution_ids: list[str],
    winning_solution_id: str | None,
) -> dict[str, object]:
    default_solution_id = f"{agent.provider}-solution"
    if phase == "spec":
        return {
            "solution_id": default_solution_id,
            "summary": f"dry-run proposal from {agent.provider}",
            "problem": "dry-run simulated problem statement",
            "proposal": f"{agent.provider} proposes a focused fix",
            "acceptance_criteria": ["task addressed", "tests or checks identified"],
            "risks": ["dry-run placeholder"],
            "files_to_touch": ["README.md"],
        }
    if phase == "review":
        preferred = "codex-solution" if "codex-solution" in candidate_solution_ids or not candidate_solution_ids else candidate_solution_ids[0]
        return {
            "solution_id": f"{agent.provider}-review",
            "preferred_solution_id": preferred,
            "summary": f"dry-run review from {agent.provider}",
            "strengths": ["narrow scope", "clear acceptance criteria"],
            "concerns": ["needs validation"],
            "recommended_changes": ["run stack-aware checks"],
        }
    if phase in {"vote", "tie_break"}:
        preferred = "codex-solution" if "codex-solution" in candidate_solution_ids or not candidate_solution_ids else candidate_solution_ids[0]
        return {
            "solution_id": f"{agent.provider}-{phase.replace('_', '-')}",
            "vote_for": preferred,
            "summary": f"dry-run {phase} from {agent.provider}",
            "reasoning": ["most concrete path", "easiest to validate"],
            "must_have_checks": ["smoke validation"],
        }
    if phase == "implement":
        return {
            "solution_id": f"{agent.provider}-implementation",
            "implemented_solution_id": winning_solution_id or default_solution_id,
            "summary": f"dry-run implementation by {agent.provider}",
            "changed_files": [],
            "validation": ["dry-run only"],
            "notes": ["no files changed in dry-run"],
        }
    return {
        "solution_id": f"{agent.provider}-{phase}",
        "summary": f"dry-run response for {agent.provider} in {phase}",
    }


def format_group_message(display_name: str, phase: str, payload: dict[str, object]) -> str:
    lines = [f"[{phase}] {display_name}"]
    scalar_labels = {
        "summary": "Pienso esto",
        "problem": "Veo este problema",
        "proposal": "Mi propuesta",
        "preferred_solution_id": "Prefiero esta solución",
        "vote_for": "Mi voto va para",
    }
    list_labels = {
        "acceptance_criteria": "Para darlo por bueno",
        "risks": "Riesgos que veo",
        "strengths": "Lo mejor de esta idea",
        "concerns": "Lo que me preocupa",
        "recommended_changes": "Ajustes que haría",
        "reasoning": "Por qué pienso eso",
        "must_have_checks": "Chequeos que no saltearía",
        "changed_files": "Archivos tocados",
        "validation": "Validaciones",
        "notes": "Notas",
    }
    for key in ("summary", "problem", "proposal", "preferred_solution_id", "vote_for"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            lines.append(f"{scalar_labels.get(key, key)}: {value.strip()}")
    for key in ("acceptance_criteria", "risks", "strengths", "concerns", "recommended_changes", "reasoning", "must_have_checks", "changed_files", "validation", "notes"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            lines.append(f"{list_labels.get(key, key)}:")
            for item in value[:6]:
                lines.append(f"- {item}")
    return "\n".join(lines)


def phase_header_text(phase: str) -> str:
    if phase == "spec":
        return "Equipo, arranquemos. Cada uno traiga su propuesta."
    if phase == "review":
        return "Ya tenemos propuestas. Hagamos review cruzado."
    if phase == "vote":
        return "Tenemos opciones sobre la mesa. Hora de votar."
    if phase.startswith("tie-break round "):
        round_number = phase.removeprefix("tie-break round ").removesuffix(" started").strip()
        return f"Seguimos empatados. Vamos con una ronda corta de desempate #{round_number}."
    if phase.startswith("forced consensus selected "):
        winner = phase.removeprefix("forced consensus selected ").strip()
        return f"No logramos 2 de 3 claros. Cerramos por pluralidad con {winner}."
    if phase.startswith("implementation started for "):
        winner = phase.removeprefix("implementation started for ").strip()
        return f"Hay acuerdo suficiente. Toca implementar {winner}."
    return phase


def count_votes(spec_payloads: list[dict[str, object]], vote_payloads: list[dict[str, object]]) -> collections.Counter[str]:
    valid_solution_ids = {
        payload["solution_id"]
        for payload in spec_payloads
        if isinstance(payload.get("solution_id"), str) and payload["solution_id"]
    }
    votes = collections.Counter()
    for payload in vote_payloads:
        vote_for = payload.get("vote_for")
        if isinstance(vote_for, str) and vote_for in valid_solution_ids:
            votes[vote_for] += 1
    return votes


def choose_winner(spec_payloads: list[dict[str, object]], vote_payloads: list[dict[str, object]]) -> str:
    votes = count_votes(spec_payloads, vote_payloads)
    for solution_id, count in votes.most_common():
        if count >= 2:
            return solution_id
    raise MulticodersError("no solution reached the required 2-of-3 consensus")


def choose_fallback_winner(spec_payloads: list[dict[str, object]], vote_payloads: list[dict[str, object]]) -> str | None:
    votes = count_votes(spec_payloads, vote_payloads)
    if not votes:
        return None
    top_two = votes.most_common(2)
    if len(top_two) == 1:
        return top_two[0][0]
    if top_two[0][1] > top_two[1][1]:
        return top_two[0][0]
    return None


def extract_solution_ids(spec_payloads: list[dict[str, object]]) -> list[str]:
    return [
        payload["solution_id"]
        for payload in spec_payloads
        if isinstance(payload.get("solution_id"), str) and payload["solution_id"]
    ]


def select_validation_commands(context: RepoContext) -> list[list[str]]:
    repo = context.repo
    commands: list[list[str]] = []
    if context.stack.key == "python":
        has_tests = any((repo / name).exists() for name in ("tests", "test"))
        if has_tests and shutil.which("pytest"):
            commands.append(["pytest", "-q"])
        elif (repo / "tests").exists():
            commands.append(["python3", "-m", "unittest", "discover", "-s", "tests"])
        commands.append(["python3", "-m", "compileall", "."])
        return commands
    if context.stack.key == "javascript":
        package_json = repo / "package.json"
        if package_json.exists():
            try:
                package = json.loads(package_json.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                package = {}
            scripts = package.get("scripts") if isinstance(package, dict) else {}
            if isinstance(scripts, dict):
                if "test" in scripts:
                    commands.append(["npm", "test", "--", "--runInBand"])
                elif "lint" in scripts:
                    commands.append(["npm", "run", "lint"])
        return commands
    if context.stack.key == "go":
        if (repo / "go.mod").exists():
            commands.append(["go", "test", "./..."])
        return commands
    if context.stack.key == "rust":
        if (repo / "Cargo.toml").exists():
            commands.append(["cargo", "test"])
        return commands
    return commands


def run_validation(context: RepoContext, dry_run: bool) -> list[ValidationResult]:
    commands = select_validation_commands(context)
    if dry_run:
        return [ValidationResult(command=" ".join(command), ok=True, output="dry-run validation skipped") for command in commands] or [
            ValidationResult(command="no validation command", ok=True, output="dry-run validation skipped")
        ]

    results: list[ValidationResult] = []
    for command in commands:
        proc = subprocess.run(
            command,
            cwd=context.repo,
            text=True,
            capture_output=True,
            check=False,
        )
        output = "\n".join(part for part in (proc.stdout.strip(), proc.stderr.strip()) if part).strip()
        results.append(
            ValidationResult(
                command=" ".join(command),
                ok=proc.returncode == 0,
                output=output or "(no output)",
            )
        )
    if not results:
        results.append(ValidationResult(command="no validation command", ok=True, output="no stack-specific validator configured"))
    return results


def append_validation_to_payload(payload: dict[str, object], validations: list[ValidationResult]) -> dict[str, object]:
    validation_lines = [f"{item.command}: {'ok' if item.ok else 'failed'}" for item in validations]
    notes = payload.get("notes")
    if not isinstance(notes, list):
        notes = []
    for item in validations:
        if not item.ok:
            notes.append(f"validation failed for {item.command}")
    payload["validation"] = validation_lines
    payload["notes"] = notes
    return payload


def collect_human_feedback(
    *,
    agents: list[AgentConfig],
    dry_run: bool,
    wait_sec: int,
    max_messages: int,
    state: TelegramState | None,
) -> HumanFeedback:
    if dry_run:
        if wait_sec <= 0:
            return HumanFeedback(messages=[], baseline_update_id=0)
        return HumanFeedback(
            messages=[
                TelegramMessage(
                    update_id=1,
                    chat_id="dry-run-chat",
                    sender_id="human-1",
                    sender_name="reviewer",
                    is_bot=False,
                    text="Prioritize the smallest safe change and verify it locally.",
                    date=0,
                )
            ],
            baseline_update_id=0,
        )
    if wait_sec <= 0:
        baseline_update_id = state.last_update_id if state is not None else 0
        return HumanFeedback(messages=[], baseline_update_id=baseline_update_id)
    observer = select_observer_bot(agents)
    if observer is None:
        baseline_update_id = state.last_update_id if state is not None else 0
        return HumanFeedback(messages=[], baseline_update_id=baseline_update_id)
    baseline_update_id = state.last_update_id if state is not None else 0
    if baseline_update_id <= 0:
        baseline_updates = observer.get_updates(timeout_sec=0)
        baseline_update_id = max((item.update_id for item in baseline_updates), default=0)
    prompt = (
        f"[multicoders] human feedback requested\n"
        f"Reply in this chat during the next {wait_sec} seconds with constraints, risks, or a preferred solution."
    )
    for agent in agents:
        send_group_message(agent, prompt, dry_run, run_id=state.last_run_id if state is not None else None)
    ignored_names = {agent.display_name for agent in agents}
    messages = observer.wait_for_human_messages(
        baseline_update_id=baseline_update_id,
        wait_sec=wait_sec,
        max_messages=max_messages,
        ignored_sender_names=ignored_names,
    )
    return HumanFeedback(messages=messages, baseline_update_id=baseline_update_id)


def feedback_payloads(feedback: HumanFeedback) -> list[dict[str, object]]:
    if not feedback.messages:
        return []
    items = [
        {
            "sender": message.sender_name,
            "text": message.text,
        }
        for message in feedback.messages
    ]
    summary = "; ".join(f"{item['sender']}: {item['text']}" for item in items[:5])
    return [
        {
            "solution_id": "human-feedback",
            "summary": summary or "human feedback received",
            "feedback_items": items,
        }
    ]


def start_brainstorming_session(
    *,
    state: TelegramState,
    topic: str,
    sender_name: str,
    repo: Path,
    dry_run: bool,
    docs_dir: str | None = None,
) -> None:
    run_id = f"brain-{uuid.uuid4().hex[:10]}"
    state.brainstorming = {
        "active": True,
        "run_id": run_id,
        "topic": topic,
        "requested_by": sender_name,
        "repo": str(repo),
        "docs_dir": docs_dir or "",
        "round_number": 1,
        "max_rounds": BRAINSTORMING_MAX_ROUNDS,
        "stage": "proposal",
        "started_at": utc_now_iso(),
        "last_turn_at": utc_now_iso(),
        "proposal_rounds": [],
        "current_proposals": [],
        "current_scorecards": [],
        "current_improvements": [],
        "current_improvement_scores": [],
        "selected_proposal_id": "",
        "selected_improvement_id": "",
        "spec_path": "",
        "status": "running",
        "transcript": [
            {"speaker": sender_name, "text": topic},
        ],
    }
    if dry_run:
        LOGGER.debug("brainstorming session started run_id=%s topic=%s", run_id, topic)


def cancel_brainstorming_session(
    *,
    state: TelegramState,
    observer_bot: TelegramBot | None,
    dry_run: bool,
    requested_by: str,
) -> None:
    session = brainstorm_session_active(state)
    if session is None:
        send_service_message(observer_bot, "[multicoders] No hay brainstorming activo para cancelar.", dry_run)
        return
    run_id = str(session.get("run_id") or "")
    state.brainstorming = None
    send_service_message(
        observer_bot,
        f"[multicoders] brainstorming cancelado por {requested_by}.",
        dry_run,
        run_id=run_id or None,
    )


def stop_any_active_session(
    *,
    state: TelegramState,
    observer_bot: TelegramBot | None,
    dry_run: bool,
    requested_by: str,
) -> None:
    if brainstorm_session_active(state) is not None:
        cancel_brainstorming_session(
            state=state,
            observer_bot=observer_bot,
            dry_run=dry_run,
            requested_by=requested_by,
        )
        return
    if active_bot_chat(state) is not None:
        stop_autonomous_bot_chat(
            state=state,
            observer_bot=observer_bot,
            dry_run=dry_run,
            requested_by=requested_by,
        )
        return
    send_service_message(observer_bot, "[multicoders] No hay sesiones activas para detener.", dry_run)


def brainstorming_status_message(state: TelegramState) -> str:
    session = brainstorm_session_active(state)
    if session is None:
        return "[multicoders] No hay brainstorming activo."
    lines = [
        "[multicoders] Brainstorming activo",
        brainstorm_summary_text(session),
        f"topic: {session.get('topic', '')}",
        f"stage: {session.get('stage', '')}",
        f"round: {session.get('round_number', '')}/{session.get('max_rounds', '')}",
    ]
    selected_proposal = session.get("selected_proposal_id")
    if isinstance(selected_proposal, str) and selected_proposal:
        lines.append(f"selected_proposal: {selected_proposal}")
    selected_improvement = session.get("selected_improvement_id")
    if isinstance(selected_improvement, str) and selected_improvement:
        lines.append(f"selected_improvement: {selected_improvement}")
    return "\n".join(lines)


def finalize_brainstorm_spec(
    *,
    session: dict[str, object],
    spec: BrainstormSpec,
    repo: Path,
    dry_run: bool,
    observer_bot: TelegramBot | None,
) -> None:
    output_path = resolve_brainstorm_output_path(session, repo)
    session["spec_path"] = str(output_path)
    session["active"] = False
    session["stage"] = "completed"
    session["status"] = "completed"
    session["last_turn_at"] = utc_now_iso()
    session["completed_at"] = utc_now_iso()
    if not dry_run:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(spec.spec_markdown, encoding="utf-8")
    else:
        LOGGER.debug("brainstorm spec write skipped mode=dry-run path=%s", output_path)
    send_service_message(
        observer_bot,
        f"[multicoders] Brainstorming finalizado. Spec: {output_path.name}",
        dry_run,
        run_id=session.get("run_id") or None,
    )


def run_brainstorming_step(
    *,
    state: TelegramState,
    agents: list[AgentConfig],
    context: RepoContext,
    dry_run: bool,
    timeout_sec: int,
    observer_bot: TelegramBot | None,
) -> None:
    session = brainstorm_session_active(state)
    if session is None or not agents:
        return
    stage = brainstorm_stage(session)
    round_number = brainstorm_current_round(session)
    run_id = str(session.get("run_id") or f"brain-{uuid.uuid4().hex[:10]}")
    topic = str(session.get("topic") or "")
    transcript = brainstorm_transcript(session)

    if stage == "proposal":
        proposals: list[dict[str, object]] = []
        for agent in agents:
            payload = ask_brainstorm_agent(
                agent=agent,
                phase="proposal",
                context=context,
                topic=topic,
                prior_payloads=transcript + list(session.get("proposal_rounds") or []),
                round_number=round_number,
                timeout_sec=timeout_sec,
                dry_run=dry_run,
                run_id=run_id,
            )
            proposals.append(
                {
                    "proposal_id": payload.get("solution_id"),
                    "author": agent.provider,
                    "display_name": agent.display_name,
                    "summary": payload.get("summary", ""),
                    "approach": payload.get("approach", ""),
                    "self_score": int(payload.get("self_score") or 0),
                    "risks": payload.get("risks", []),
                    "improvement_ideas": payload.get("improvement_ideas", []),
                }
            )
            transcript.append({"speaker": agent.display_name, "text": str(payload.get("summary", ""))})
        session["current_proposals"] = proposals
        session["current_scorecards"] = []
        session["current_improvements"] = []
        session["current_improvement_scores"] = []
        store_brainstorm_transcript(session, transcript)
        brainstorm_next_stage(session, "score")
        state.brainstorming = session
        return

    if stage == "score":
        proposals = list(session.get("current_proposals") or [])
        if not proposals:
            brainstorm_next_stage(session, "proposal")
            state.brainstorming = session
            return
        score_payloads: list[dict[str, object]] = []
        for agent in agents:
            payload = ask_brainstorm_agent(
                agent=agent,
                phase="score",
                context=context,
                topic=topic,
                prior_payloads=proposals + list(session.get("proposal_rounds") or []),
                round_number=round_number,
                timeout_sec=timeout_sec,
                dry_run=dry_run,
                run_id=run_id,
            )
            score_payloads.append(
                {
                    "scorer": agent.provider,
                    "solution_id": payload.get("solution_id"),
                    "scores": payload.get("scores", {}),
                    "best_proposal_id": payload.get("best_proposal_id", ""),
                    "summary": payload.get("summary", ""),
                    "reasons": payload.get("reasons", []),
                }
            )
        session["current_scorecards"] = score_payloads
        ranking = brainstorm_aggregate_proposal_scores(proposals, score_payloads)
        session["proposal_ranking"] = ranking
        session["selected_proposal_id"] = ranking[0]["proposal_id"] if ranking else ""
        store_brainstorm_transcript(session, transcript + [{"speaker": "system", "text": f"selected proposal {session['selected_proposal_id']}"}])
        brainstorm_next_stage(session, "proposal_vote")
        state.brainstorming = session
        return

    if stage == "proposal_vote":
        ranking = list(session.get("proposal_ranking") or [])
        if not ranking:
            brainstorm_next_stage(session, "proposal")
            state.brainstorming = session
            return
        selected_proposal_id = str(session.get("selected_proposal_id") or ranking[0]["proposal_id"])
        vote_payloads: list[dict[str, object]] = []
        for agent in agents:
            payload = ask_brainstorm_agent(
                agent=agent,
                phase="proposal_vote",
                context=context,
                topic=topic,
                prior_payloads=ranking,
                round_number=round_number,
                timeout_sec=timeout_sec,
                dry_run=dry_run,
                run_id=run_id,
                selected_solution_id=selected_proposal_id,
            )
            vote_payloads.append(payload)
        unanimous = brainstorm_unanimous_choice(vote_payloads, "vote_for")
        if unanimous is None:
            append_brainstorm_round_snapshot(session)
            if round_number >= BRAINSTORMING_MAX_ROUNDS:
                raise MulticodersError("brainstorming did not reach unanimous proposal agreement within the maximum rounds")
            session["round_number"] = round_number + 1
            session["current_proposals"] = []
            session["current_scorecards"] = []
            session["current_improvements"] = []
            session["current_improvement_scores"] = []
            session["proposal_ranking"] = []
            brainstorm_next_stage(session, "proposal")
            state.brainstorming = session
            send_service_message(observer_bot, "[multicoders] No hubo unanimidad. Arranco una nueva ronda de propuestas.", dry_run, run_id=run_id)
            return
        session["selected_proposal_id"] = unanimous
        append_brainstorm_round_snapshot(session)
        session["current_improvements"] = []
        session["current_improvement_scores"] = []
        brainstorm_next_stage(session, "improvement")
        state.brainstorming = session
        return

    if stage == "improvement":
        selected_solution_id = str(session.get("selected_proposal_id") or "")
        improvements: list[dict[str, object]] = []
        for agent in agents:
            payload = ask_brainstorm_agent(
                agent=agent,
                phase="improvement",
                context=context,
                topic=topic,
                prior_payloads=list(session.get("proposal_ranking") or []) + list(session.get("proposal_rounds") or []),
                round_number=round_number,
                timeout_sec=timeout_sec,
                dry_run=dry_run,
                run_id=run_id,
                selected_solution_id=selected_solution_id,
            )
            improvements.append(
                {
                    "improvement_id": payload.get("improvement_id"),
                    "author": agent.provider,
                    "display_name": agent.display_name,
                    "target_solution_id": payload.get("target_solution_id", ""),
                    "summary": payload.get("summary", ""),
                    "improvement": payload.get("improvement", ""),
                    "self_score": int(payload.get("self_score") or 0),
                    "tradeoffs": payload.get("tradeoffs", []),
                }
            )
        session["current_improvements"] = improvements
        ranking = brainstorm_aggregate_improvement_scores(improvements, [])
        session["improvement_ranking"] = ranking
        session["selected_improvement_id"] = ranking[0]["improvement_id"] if ranking else ""
        brainstorm_next_stage(session, "improvement_score")
        state.brainstorming = session
        return

    if stage == "improvement_score":
        improvements = list(session.get("current_improvements") or [])
        if not improvements:
            brainstorm_next_stage(session, "improvement")
            state.brainstorming = session
            return
        score_payloads: list[dict[str, object]] = []
        for agent in agents:
            payload = ask_brainstorm_agent(
                agent=agent,
                phase="score",
                context=context,
                topic=topic,
                prior_payloads=improvements + list(session.get("proposal_rounds") or []),
                round_number=round_number,
                timeout_sec=timeout_sec,
                dry_run=dry_run,
                run_id=run_id,
            )
            score_payloads.append(
                {
                    "scorer": agent.provider,
                    "solution_id": payload.get("solution_id"),
                    "scores": payload.get("scores", {}),
                    "best_proposal_id": payload.get("best_proposal_id", ""),
                    "summary": payload.get("summary", ""),
                    "reasons": payload.get("reasons", []),
                }
            )
        ranking = brainstorm_aggregate_improvement_scores(improvements, score_payloads)
        session["improvement_ranking"] = ranking
        session["selected_improvement_id"] = ranking[0]["improvement_id"] if ranking else ""
        brainstorm_next_stage(session, "improvement_vote")
        state.brainstorming = session
        return

    if stage == "improvement_vote":
        ranking = list(session.get("improvement_ranking") or [])
        if not ranking:
            brainstorm_next_stage(session, "improvement")
            state.brainstorming = session
            return
        selected_improvement_id = str(session.get("selected_improvement_id") or ranking[0]["improvement_id"])
        vote_payloads: list[dict[str, object]] = []
        for agent in agents:
            payload = ask_brainstorm_agent(
                agent=agent,
                phase="improvement_vote",
                context=context,
                topic=topic,
                prior_payloads=ranking + list(session.get("proposal_rounds") or []),
                round_number=round_number,
                timeout_sec=timeout_sec,
                dry_run=dry_run,
                run_id=run_id,
                selected_solution_id=str(session.get("selected_proposal_id") or ""),
                selected_improvement_id=selected_improvement_id,
            )
            vote_payloads.append(payload)
        unanimous = brainstorm_unanimous_choice(vote_payloads, "vote_for")
        if unanimous is None:
            append_brainstorm_round_snapshot(session)
            if round_number >= BRAINSTORMING_MAX_ROUNDS:
                raise MulticodersError("brainstorming did not reach unanimous improvement agreement within the maximum rounds")
            session["round_number"] = round_number + 1
            session["current_proposals"] = []
            session["current_scorecards"] = []
            session["current_improvements"] = []
            session["current_improvement_scores"] = []
            session["proposal_ranking"] = []
            session["improvement_ranking"] = []
            brainstorm_next_stage(session, "proposal")
            state.brainstorming = session
            send_service_message(observer_bot, "[multicoders] La mejora no fue unánime. Vuelvo a proponer alternativas.", dry_run, run_id=run_id)
            return
        session["selected_improvement_id"] = unanimous
        append_brainstorm_round_snapshot(session)
        brainstorm_next_stage(session, "spec")
        state.brainstorming = session
        return

    if stage == "spec":
        ranking = list(session.get("improvement_ranking") or session.get("proposal_ranking") or [])
        chosen_agent = next((agent for agent in agents if agent.provider == str(session.get("selected_improvement_id") or "").split("-", 1)[0]), agents[0])
        payload = ask_brainstorm_agent(
            agent=chosen_agent,
            phase="spec",
            context=context,
            topic=topic,
            prior_payloads=ranking,
            round_number=round_number,
            timeout_sec=timeout_sec,
            dry_run=dry_run,
            run_id=run_id,
            selected_solution_id=str(session.get("selected_proposal_id") or ""),
            selected_improvement_id=str(session.get("selected_improvement_id") or ""),
        )
        spec = BrainstormSpec(
            spec_title=str(payload.get("spec_title") or f"Spec for {topic}"),
            spec_markdown=str(payload.get("spec_markdown") or f"# Spec for {topic}\n"),
            summary=str(payload.get("summary") or ""),
            file_name=str(payload.get("file_name") or f"{slugify_text(topic)}.md"),
        )
        finalize_brainstorm_spec(
            session=session,
            spec=spec,
            repo=context.repo,
            dry_run=dry_run,
            observer_bot=observer_bot,
        )
        session["spec"] = {
            "spec_title": spec.spec_title,
            "spec_markdown": spec.spec_markdown,
            "summary": spec.summary,
            "file_name": spec.file_name,
        }
        state.brainstorming = session
        return

    state.brainstorming = session


def print_json(title: str, payload: object) -> None:
    print(f"{title}:")
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def prepare_provider_args(args: argparse.Namespace) -> None:
    load_env_file(Path(args.env_file).resolve())
    provider_names = parse_provider_names(args.providers)
    if len(provider_names) != 3:
        raise SystemExit(f"Expected exactly 3 providers, found: {', '.join(provider_names) or 'none'}")
    if args.dry_run:
        args.providers = provider_names
        return
    active_providers = available_providers(provider_names)
    if len(active_providers) != 3:
        missing = [name for name in provider_names if name not in active_providers]
        raise SystemExit(f"Expected 3 available providers. Missing binaries for: {', '.join(missing) or 'none'}")
    args.providers = active_providers


def prepare_telegram_provider_args(args: argparse.Namespace) -> None:
    load_env_file(Path(args.env_file).resolve())
    provider_names = parse_provider_names(args.providers)
    if len(provider_names) != 3:
        raise SystemExit(f"Expected exactly 3 providers, found: {', '.join(provider_names) or 'none'}")
    args.providers = provider_names


def add_shared_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--providers",
        default="codex,claude,gemini",
        help="Comma-separated provider order",
    )
    parser.add_argument("--env-file", default=ENV_FILE_NAME, help="Path to .env file")
    parser.add_argument("--telegram-state-file", default=None, help="Path to persisted Telegram state JSON")
    parser.add_argument("--provider-timeout-sec", type=positive_int, default=600, help="Timeout for each provider call")
    parser.add_argument(
        "--chat-provider-timeout-sec",
        type=positive_int,
        default=CHAT_PROVIDER_TIMEOUT_SEC,
        help="Per-turn timeout (seconds) for chat replies. Caps --provider-timeout-sec for chat turns only.",
    )
    parser.add_argument(
        "--provider-cooldown-sec",
        type=positive_int,
        default=PROVIDER_COOLDOWN_SEC,
        help="Default sleep duration (seconds) when a provider hits a quota/rate-limit error and the API does not specify a retry-after.",
    )
    parser.add_argument("--tie-break-rounds", type=non_negative_int, default=1, help="Extra voting rounds if the first vote does not reach 2-of-3")
    parser.add_argument("--feedback-wait-sec", type=non_negative_int, default=0, help="Seconds to wait for human Telegram feedback before voting")
    parser.add_argument("--feedback-max-messages", type=positive_int, default=6, help="Maximum number of human Telegram messages to capture")
    parser.add_argument("--codex-model", default=None, help="Optional Codex model override")
    parser.add_argument("--claude-model", default=None, help="Optional Claude model override")
    parser.add_argument("--gemini-model", default=None, help="Optional Gemini model override")
    parser.add_argument("--dry-run", action="store_true", help="Skip provider and Telegram network calls")
    parser.add_argument("--no-telegram", action="store_true", help="Run the council without Telegram mirroring")
    parser.add_argument("--log-level", default="INFO", help="Logging level: DEBUG, INFO, WARNING, ERROR")
    parser.add_argument("--log-file", default=None, help="Optional service or run log file path")


def add_telegram_only_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--providers",
        default="codex,claude,gemini",
        help="Comma-separated provider order",
    )
    parser.add_argument("--env-file", default=ENV_FILE_NAME, help="Path to .env file")
    parser.add_argument("--dry-run", action="store_true", help="Skip Telegram network calls and print planned messages")
    parser.add_argument("--log-level", default="INFO", help="Logging level: DEBUG, INFO, WARNING, ERROR")
    parser.add_argument("--log-file", default=None, help="Optional log file path")


def add_telegram_discovery_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--providers",
        default="codex,claude,gemini",
        help="Comma-separated provider order",
    )
    parser.add_argument("--env-file", default=ENV_FILE_NAME, help="Path to .env file")
    parser.add_argument("--limit", type=positive_int, default=20, help="Maximum recent updates to inspect")
    parser.add_argument("--dry-run", action="store_true", help="Skip Telegram network calls and print simulated discovery data")
    parser.add_argument("--log-level", default="INFO", help="Logging level: DEBUG, INFO, WARNING, ERROR")
    parser.add_argument("--log-file", default=None, help="Optional log file path")


def add_brainstorm_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", required=True, help="Path to the target git repository")
    parser.add_argument("--topic", required=True, help="Brainstorming topic or design problem")
    parser.add_argument("--env-file", default=ENV_FILE_NAME, help="Path to .env file")
    parser.add_argument("--providers", default="codex,claude,gemini", help="Comma-separated provider order")
    parser.add_argument("--provider-timeout-sec", type=positive_int, default=600, help="Timeout for each provider call")
    parser.add_argument("--dry-run", action="store_true", help="Skip provider and Telegram network calls")
    parser.add_argument("--no-telegram", action="store_true", help="Run brainstorming without Telegram mirroring")
    parser.add_argument("--docs-dir", default=None, help="Directory where the final brainstorming spec is written")
    parser.add_argument("--log-level", default="INFO", help="Logging level: DEBUG, INFO, WARNING, ERROR")
    parser.add_argument("--log-file", default=None, help="Optional log file path")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    argv = list(argv or sys.argv[1:])
    root_commands = {"run", "service", "send-test-messages", "discover-telegram-chat", "brainstorming"}
    if not argv or (argv[0] not in root_commands and argv[0] not in {"-h", "--help", "--version"}):
        argv = ["run", *argv]

    parser = argparse.ArgumentParser(
        prog="multicoders",
        description="Run Codex, Claude, and Gemini over a repository, discuss via Telegram, and converge on a 2-of-3 solution.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run a single council session")
    run_parser.add_argument("--repo", required=True, help="Path to the target git repository")
    run_parser.add_argument("--task", required=True, help="Bugfix or feature request to discuss and implement")
    run_parser.add_argument("--task-type", choices=["bugfix", "feature"], default="bugfix", help="Classify the task to bias the prompts")
    add_shared_run_args(run_parser)

    service_parser = subparsers.add_parser("service", help="Run the persistent Telegram-backed service")
    service_parser.add_argument("--db-file", default=None, help="Path to SQLite database file")
    service_parser.add_argument("--poll-sec", type=positive_int, default=SERVICE_POLL_SEC, help="Polling interval when idle")
    service_parser.add_argument("--once", action="store_true", help="Process one polling cycle and exit")
    add_shared_run_args(service_parser)

    test_parser = subparsers.add_parser("send-test-messages", help="Send a real test message from each bot to the configured Telegram group")
    test_parser.add_argument("--message", default="multicoders connectivity test", help="Base message body for the bot test")
    add_telegram_only_args(test_parser)

    discover_parser = subparsers.add_parser("discover-telegram-chat", help="Inspect Telegram updates and list candidate chat ids and topic ids")
    add_telegram_discovery_args(discover_parser)

    brainstorm_parser = subparsers.add_parser("brainstorming", help="Run a design-only brainstorming session and write a spec")
    add_brainstorm_args(brainstorm_parser)

    return parser.parse_args(argv)


def execute_council_session(args: argparse.Namespace, *, repo: Path, task: str, task_type: str) -> dict[str, object]:
    context = build_repo_context(repo, allow_non_git=args.dry_run)
    agents = build_agent_configs(args)
    lead_provider = getattr(args, "lead_provider", None)
    if lead_provider:
        agents = sorted(agents, key=lambda agent: 0 if agent.provider == lead_provider else 1)
    run_id = uuid.uuid4().hex[:12]
    started_at = utc_now_iso()
    state_file = Path(args.telegram_state_file).expanduser().resolve() if args.telegram_state_file else context.repo / ".multicoders" / "telegram-state.json"
    telegram_state = load_telegram_state(state_file)
    telegram_state.last_run_id = run_id
    telegram_state.last_repo = str(context.repo)
    telegram_state.last_task = task
    telegram_state.last_started_at = started_at
    save_telegram_state(telegram_state)
    if not args.dry_run and not args.no_telegram:
        missing = [agent.provider for agent in agents if agent.bot is None]
        if missing:
            raise MulticodersError(
                "Telegram is required in real mode. Missing bot configuration for: "
                + ", ".join(missing)
            )

    post_phase_header(agents, "spec discussion started", args.dry_run, run_id=run_id)
    spec_payloads = [
        ask_agent(
            agent=agent,
            phase="spec",
            context=context,
            task_type=task_type,
            task=task,
            prior_payloads=[],
            timeout_sec=args.provider_timeout_sec,
            dry_run=args.dry_run,
            run_id=run_id,
        )
        for agent in agents
    ]

    post_phase_header(agents, "peer review started", args.dry_run, run_id=run_id)
    review_payloads = [
        ask_agent(
            agent=agent,
            phase="review",
            context=context,
            task_type=task_type,
            task=task,
            prior_payloads=spec_payloads,
            timeout_sec=args.provider_timeout_sec,
            dry_run=args.dry_run,
            run_id=run_id,
        )
        for agent in agents
    ]

    human_feedback = collect_human_feedback(
        agents=agents,
        dry_run=args.dry_run,
        wait_sec=args.feedback_wait_sec,
        max_messages=args.feedback_max_messages,
        state=telegram_state,
    )
    human_feedback_payloads = feedback_payloads(human_feedback)
    observed_update_id = max((item.update_id for item in human_feedback.messages), default=human_feedback.baseline_update_id)
    telegram_state.last_update_id = max(telegram_state.last_update_id, observed_update_id)
    telegram_state.last_feedback_count = len(human_feedback.messages)
    save_telegram_state(telegram_state)

    post_phase_header(agents, "vote started", args.dry_run, run_id=run_id)
    solution_ids = extract_solution_ids(spec_payloads)
    vote_payloads = [
        ask_agent(
            agent=agent,
            phase="vote",
            context=context,
            task_type=task_type,
            task=task,
            prior_payloads=spec_payloads + review_payloads + human_feedback_payloads,
            timeout_sec=args.provider_timeout_sec,
            dry_run=args.dry_run,
            candidate_solution_ids=solution_ids,
            run_id=run_id,
        )
        for agent in agents
    ]

    all_vote_payloads = list(vote_payloads)
    forced_consensus = False
    try:
        winning_solution_id = choose_winner(spec_payloads, all_vote_payloads)
    except MulticodersError:
        winning_solution_id = ""
        for round_number in range(1, args.tie_break_rounds + 1):
            post_phase_header(agents, f"tie-break round {round_number} started", args.dry_run, run_id=run_id)
            tie_break_payloads = [
                ask_agent(
                    agent=agent,
                    phase="tie_break",
                    context=context,
                    task_type=task_type,
                    task=task,
                    prior_payloads=spec_payloads + review_payloads + human_feedback_payloads + all_vote_payloads,
                    timeout_sec=args.provider_timeout_sec,
                    dry_run=args.dry_run,
                    candidate_solution_ids=solution_ids,
                    run_id=run_id,
                )
                for agent in agents
            ]
            all_vote_payloads.extend(tie_break_payloads)
            try:
                winning_solution_id = choose_winner(spec_payloads, tie_break_payloads)
                break
            except MulticodersError:
                continue
        if not winning_solution_id:
            fallback_winner = choose_fallback_winner(spec_payloads, all_vote_payloads)
            if fallback_winner:
                winning_solution_id = fallback_winner
                forced_consensus = True
                LOGGER.warning("forced consensus run_id=%s winner=%s votes=%s", run_id, winning_solution_id, dict(count_votes(spec_payloads, all_vote_payloads)))
                post_phase_header(agents, f"forced consensus selected {winning_solution_id}", args.dry_run, run_id=run_id)
            else:
                raise MulticodersError("No consensus after configured tie-break rounds")
    winner = next(agent for agent, spec in zip(agents, spec_payloads, strict=True) if spec.get("solution_id") == winning_solution_id)

    post_phase_header(agents, f"implementation started for {winning_solution_id}", args.dry_run, run_id=run_id)
    implementation_payload = ask_agent(
        agent=winner,
        phase="implement",
        context=context,
        task_type=task_type,
        task=task,
        prior_payloads=spec_payloads + review_payloads + human_feedback_payloads + all_vote_payloads,
        timeout_sec=args.provider_timeout_sec,
        dry_run=args.dry_run,
        winning_solution_id=winning_solution_id,
        run_id=run_id,
    )
    validations = run_validation(context, args.dry_run)
    validation_ok = all(item.ok for item in validations)
    implementation_payload = append_validation_to_payload(implementation_payload, validations)
    send_group_message(winner, format_group_message(winner.display_name, "validation", implementation_payload), args.dry_run, run_id=run_id)

    finished_at = utc_now_iso()
    telegram_state.last_finished_at = finished_at
    append_discussion_run(
        telegram_state,
        run_id=run_id,
        repo=context.repo,
        task=task,
        started_at=started_at,
        finished_at=finished_at,
        winner=winning_solution_id,
        feedback_messages=human_feedback.messages,
    )
    save_telegram_state(telegram_state)

    return {
        "specs": spec_payloads,
        "reviews": review_payloads,
        "human_feedback": human_feedback_payloads,
        "votes": all_vote_payloads,
        "implementation": implementation_payload,
        "winner": winning_solution_id,
        "lead_provider": lead_provider,
        "forced_consensus": forced_consensus,
        "validation_ok": validation_ok,
        "telegram_state": telegram_state.to_dict(),
        "run_id": run_id,
    }


def process_service_commands(
    *,
    bot: TelegramBot | None,
    conn,
    telegram_state: TelegramState,
    dry_run: bool,
    providers: list[str],
    agents: list[AgentConfig],
    provider_timeout_sec: int,
    conversation_repo: Path,
) -> None:
    listeners = iter_listener_bots(bot, agents)
    if dry_run or not listeners:
        LOGGER.debug("service command processing skipped dry_run=%s bot_count=%s", dry_run, len(listeners))
        return
    brainstorm_context = build_repo_context(conversation_repo, allow_non_git=True)
    gathered: list[tuple[TelegramBot, TelegramMessage]] = []
    total_updates = 0
    offsets = dict(telegram_state.bot_offsets or {})
    for listener in listeners:
        listener_name = getattr(listener, "name", listener.__class__.__name__)
        offset = int(offsets.get(listener_name, 0) or 0) + 1
        updates = listener.get_updates(offset=offset, timeout_sec=0)
        total_updates += len(updates)
        LOGGER.debug("service polled bot=%s updates=%d offset=%d", listener_name, len(updates), offset)
        for update in updates:
            telegram_state.last_update_id = max(telegram_state.last_update_id, update.update_id)
            offsets[listener_name] = max(int(offsets.get(listener_name, 0) or 0), update.update_id)
            gathered.append((listener, update))
    telegram_state.bot_offsets = offsets
    LOGGER.debug("service polled %d updates", total_updates)

    seen_keys = set(telegram_state.recent_message_keys or [])
    autonomous_chat_changed = False
    saw_bot_update_in_scope = False
    for listener, update in sorted(gathered, key=lambda item: (item[1].date, item[1].sender_id, item[1].text, item[1].update_id)):
        listener_name = getattr(listener, "name", listener.__class__.__name__)
        if not message_matches_listener_scope(update, listener) or not update.text:
            continue
        if update.is_bot:
            saw_bot_update_in_scope = True
            LOGGER.debug("service skipped bot update bot=%s sender=%s text=%s", listener_name, update.sender_name, preview_text(update.text))
            continue
        fingerprint = message_fingerprint(update)
        if fingerprint in seen_keys:
            LOGGER.debug("service skipped duplicate update bot=%s sender=%s text=%s", listener_name, update.sender_name, preview_text(update.text))
            continue
        seen_keys.add(fingerprint)
        remember_message_key(telegram_state, fingerprint)
        if parse_silence_command(update.text):
            LOGGER.info("autonomous bot chat stop requested sender=%s", update.sender_name)
            stop_any_active_session(
                state=telegram_state,
                observer_bot=bot,
                dry_run=dry_run,
                requested_by=update.sender_name,
            )
            autonomous_chat_changed = True
            continue
        brainstorming_topic = parse_brainstorming_command(update.text)
        if brainstorming_topic is not None:
            if brainstorm_session_active(telegram_state) is not None or active_bot_chat(telegram_state) is not None:
                send_service_message(
                    bot,
                    "[multicoders] Ya hay una sesión activa. Cancélala con /brainstorming-cancel o silencio antes de iniciar otra.",
                    dry_run,
                )
                continue
            LOGGER.info("brainstorming requested sender=%s topic=%s", update.sender_name, brainstorming_topic)
            start_brainstorming_session(
                state=telegram_state,
                topic=brainstorming_topic,
                sender_name=update.sender_name,
                repo=conversation_repo,
                dry_run=dry_run,
            )
            current_session = brainstorm_session_active(telegram_state)
            send_service_message(
                bot,
                f"[multicoders] brainstorming iniciado para: {brainstorming_topic}",
                dry_run,
                run_id=current_session.get("run_id") if current_session is not None else None,
            )
            autonomous_chat_changed = True
            continue
        if matches_brainstorming_status_command(update.text):
            send_service_message(bot, brainstorming_status_message(telegram_state), dry_run)
            continue
        if matches_brainstorming_cancel_command(update.text):
            cancel_brainstorming_session(
                state=telegram_state,
                observer_bot=bot,
                dry_run=dry_run,
                requested_by=update.sender_name,
            )
            autonomous_chat_changed = True
            continue
        media_command = parse_media_command(update.text)
        if media_command is not None:
            kind, key, caption = media_command
            catalog = load_media_catalog(kind)
            asset = catalog.get(key)
            if asset is None:
                send_service_message(
                    bot,
                    f"[multicoders-service] No tengo asset configurado para {kind}:{key}.",
                    dry_run,
                )
                continue
            sender_agent = random.choice(agents)
            send_group_media(
                sender_agent,
                RichMediaRequest(kind=kind, key=key, asset=asset, caption=caption),
                dry_run,
            )
            LOGGER.info("media command sent kind=%s key=%s provider=%s requester=%s", kind, key, sender_agent.provider, update.sender_name)
            continue
        random_task_command = parse_random_task_command(update.text)
        if random_task_command is not None:
            repo_path, task_type, task_text = random_task_command
            lead_provider = random.choice(providers)
            task_id = create_task(
                conn,
                repo_path=str(Path(repo_path).expanduser()),
                task_type=task_type,
                task_text=task_text,
                lead_provider=lead_provider,
                created_at=utc_now_iso(),
                requester=update.sender_name,
                request_update_id=update.update_id,
            )
            LOGGER.info("random task queued task_id=%s repo=%s type=%s requester=%s lead=%s", task_id, repo_path, task_type, update.sender_name, lead_provider)
            send_service_message(
                bot,
                format_service_event(
                    event="pending",
                    task_id=task_id,
                    status="pending",
                    repo_path=repo_path,
                    task_type=task_type,
                    task_text=task_text,
                    lead_provider=lead_provider,
                    details=f"approve with /approve {task_id}",
                ),
                dry_run,
            )
            continue
        task_command = parse_task_command(update.text)
        if task_command is not None:
            repo_path, task_type, task_text = task_command
            task_id = create_task(
                conn,
                repo_path=str(Path(repo_path).expanduser()),
                task_type=task_type,
                task_text=task_text,
                lead_provider=None,
                created_at=utc_now_iso(),
                requester=update.sender_name,
                request_update_id=update.update_id,
            )
            LOGGER.info("task queued task_id=%s repo=%s type=%s requester=%s", task_id, repo_path, task_type, update.sender_name)
            send_service_message(
                bot,
                format_service_event(
                    event="pending",
                    task_id=task_id,
                    status="pending",
                    repo_path=repo_path,
                    task_type=task_type,
                    task_text=task_text,
                    lead_provider=None,
                    details=f"approve with /approve {task_id}",
                ),
                dry_run,
            )
            continue
        approve_id = parse_integer_command(update.text, "approve")
        if approve_id is not None:
            task = get_task(conn, approve_id)
            if task is None:
                LOGGER.warning("approve requested for unknown task_id=%s", approve_id)
                send_service_message(bot, format_service_event(event="not_found", task_id=approve_id, details="task not found"), dry_run)
            elif task.status not in {"pending", "rejected"}:
                LOGGER.warning("approve skipped task_id=%s status=%s", approve_id, task.status)
                send_service_message(bot, format_service_event(event="approve_skipped", task_id=approve_id, status=task.status, details="task not in approvable state"), dry_run)
            else:
                update_task_status(conn, task_id=approve_id, status="approved", updated_at=utc_now_iso(), approved_at=utc_now_iso())
                LOGGER.info("task approved task_id=%s", approve_id)
                send_service_message(bot, format_service_event(event="approved", task_id=approve_id, status="approved"), dry_run)
            continue
        reject_id = parse_integer_command(update.text, "reject")
        if reject_id is not None:
            task = get_task(conn, reject_id)
            if task is None:
                LOGGER.warning("reject requested for unknown task_id=%s", reject_id)
                send_service_message(bot, format_service_event(event="not_found", task_id=reject_id, details="task not found"), dry_run)
            elif task.status in {"done", "running"}:
                LOGGER.warning("reject skipped task_id=%s status=%s", reject_id, task.status)
                send_service_message(bot, format_service_event(event="reject_skipped", task_id=reject_id, status=task.status, details="task cannot be rejected from this state"), dry_run)
            else:
                update_task_status(conn, task_id=reject_id, status="rejected", updated_at=utc_now_iso())
                LOGGER.info("task rejected task_id=%s", reject_id)
                send_service_message(bot, format_service_event(event="rejected", task_id=reject_id, status="rejected"), dry_run)
            continue
        retry_id = parse_integer_command(update.text, "retry")
        if retry_id is not None:
            task = get_task(conn, retry_id)
            if task is None:
                LOGGER.warning("retry requested for unknown task_id=%s", retry_id)
                send_service_message(bot, format_service_event(event="not_found", task_id=retry_id, details="task not found"), dry_run)
            elif retry_task(conn, task_id=retry_id, updated_at=utc_now_iso()):
                LOGGER.info("task retried task_id=%s", retry_id)
                send_service_message(bot, format_service_event(event="retried", task_id=retry_id, status="pending"), dry_run)
            else:
                LOGGER.warning("retry skipped task_id=%s status=%s", retry_id, task.status)
                send_service_message(bot, format_service_event(event="retry_skipped", task_id=retry_id, status=task.status, details="only failed or rejected tasks can be retried"), dry_run)
            continue
        if matches_simple_command(update.text, "status"):
            recent = list_recent_tasks(conn)
            LOGGER.info("status requested recent_count=%s", len(recent))
            if not recent:
                send_service_message(bot, format_service_event(event="status", details="no tasks yet"), dry_run)
            else:
                lines = ["[multicoders-service] recent tasks"]
                for item in recent[:6]:
                    lines.append(f"#{item.id} {item.status} {item.task_type} repo={item.repo_path} text={item.task_text[:60]}")
                send_service_message(bot, "\n".join(lines), dry_run)
            continue
        if parse_quorum_command(update.text):
            send_service_message(
                bot,
                format_quorum_status(telegram_state, providers=providers, agents=agents),
                dry_run,
            )
            continue
        wake_target = parse_wake_command(update.text)
        if wake_target is not None:
            if wake_target not in known_provider_names():
                send_service_message(
                    bot,
                    f"[multicoders-service] {wake_target} no es un provider conocido.",
                    dry_run,
                )
            else:
                force_wake_provider(
                    telegram_state,
                    provider=wake_target,
                    observer_bot=bot,
                    dry_run=dry_run,
                    conn=conn,
                )
            continue
        promote_args = parse_promote_command(update.text)
        if promote_args is not None:
            promote_provider, promote_task_id = promote_args
            if promote_provider not in known_provider_names():
                send_service_message(
                    bot,
                    f"[multicoders-service] {promote_provider} no es un provider conocido.",
                    dry_run,
                )
                continue
            task = get_task(conn, promote_task_id)
            if task is None:
                send_service_message(
                    bot,
                    f"[multicoders-service] tarea #{promote_task_id} no existe.",
                    dry_run,
                )
                continue
            if task.status != "paused_quota":
                send_service_message(
                    bot,
                    f"[multicoders-service] tarea #{promote_task_id} no está pausada (status={task.status}).",
                    dry_run,
                )
                continue
            if resume_paused_task(
                conn,
                task_id=promote_task_id,
                updated_at=utc_now_iso(),
                lead_provider=promote_provider,
            ):
                LOGGER.info(
                    "task promoted task_id=%s lead=%s",
                    promote_task_id,
                    promote_provider,
                )
                send_service_message(
                    bot,
                    f"[multicoders-service] #{promote_task_id} promovida: {promote_provider} cierra la decisión.",
                    dry_run,
                    run_id=f"task-{promote_task_id}",
                )
            continue
        resume_id = parse_integer_command(update.text, "resume")
        if resume_id is not None:
            task = get_task(conn, resume_id)
            if task is None:
                send_service_message(
                    bot,
                    f"[multicoders-service] tarea #{resume_id} no existe.",
                    dry_run,
                )
            elif task.status != "paused_quota":
                send_service_message(
                    bot,
                    f"[multicoders-service] #{resume_id} no está pausada (status={task.status}).",
                    dry_run,
                )
            elif resume_paused_task(conn, task_id=resume_id, updated_at=utc_now_iso()):
                LOGGER.info("task resumed task_id=%s", resume_id)
                send_service_message(
                    bot,
                    f"[multicoders-service] #{resume_id} reanudada.",
                    dry_run,
                    run_id=f"task-{resume_id}",
                )
            continue
        cancel_id = parse_integer_command(update.text, "cancel")
        if cancel_id is not None:
            task = get_task(conn, cancel_id)
            if task is None:
                send_service_message(
                    bot,
                    f"[multicoders-service] tarea #{cancel_id} no existe.",
                    dry_run,
                )
            elif task.status not in {"paused_quota", "pending", "approved"}:
                send_service_message(
                    bot,
                    f"[multicoders-service] #{cancel_id} no se puede cancelar (status={task.status}).",
                    dry_run,
                )
            else:
                update_task_status(
                    conn,
                    task_id=cancel_id,
                    status="rejected",
                    updated_at=utc_now_iso(),
                )
                LOGGER.info("task cancelled task_id=%s", cancel_id)
                send_service_message(
                    bot,
                    f"[multicoders-service] #{cancel_id} cancelada.",
                    dry_run,
                    run_id=f"task-{cancel_id}",
                )
            continue
        chat_text = parse_chat_command(update.text)
        if chat_text is not None:
            LOGGER.info("group chat command triggered sender=%s", update.sender_name)
            try:
                start_autonomous_bot_chat(
                    state=telegram_state,
                    agents=agents,
                    sender_name=update.sender_name,
                    user_message=chat_text,
                    repo=conversation_repo,
                    timeout_sec=provider_timeout_sec,
                    dry_run=dry_run,
                    observer_bot=bot,
                    conn=conn,
                )
                autonomous_chat_changed = True
            except Exception as exc:
                LOGGER.exception("group chat command failed sender=%s", update.sender_name)
                log_exception_details(exc)
                send_service_message(
                    bot,
                    f"[multicoders-service] No pude cerrar la conversación.\ndetails: {short_exception_text(exc)}",
                    dry_run,
                )
            continue
        if not update.text.startswith("/"):
            LOGGER.info("single bot response triggered sender=%s", update.sender_name)
            try:
                run_single_human_response(
                    agents=agents,
                    sender_name=update.sender_name,
                    user_message=update.text,
                    repo=conversation_repo,
                    timeout_sec=provider_timeout_sec,
                    dry_run=dry_run,
                    observer_bot=bot,
                    state=telegram_state,
                    conn=conn,
                )
                autonomous_chat_changed = True
            except Exception as exc:
                LOGGER.exception("group conversation failed sender=%s", update.sender_name)
                log_exception_details(exc)
                send_service_message(
                    bot,
                    f"[multicoders-service] No pude cerrar la conversación.\ndetails: {short_exception_text(exc)}",
                    dry_run,
                )
    active_brainstorm = brainstorm_session_active(telegram_state)
    brainstorm_paused = (
        isinstance(active_brainstorm, dict)
        and isinstance(active_brainstorm.get("paused_quota"), dict)
    )
    if not autonomous_chat_changed and active_brainstorm is not None and not brainstorm_paused:
        try:
            run_brainstorming_step(
                state=telegram_state,
                agents=agents,
                context=brainstorm_context,
                dry_run=dry_run,
                timeout_sec=provider_timeout_sec,
                observer_bot=bot,
            )
        except ProviderQuotaError as exc:
            until = exc.retry_at or (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=PROVIDER_COOLDOWN_SEC))
            LOGGER.warning(
                "brainstorming paused by quota provider=%s until=%s",
                exc.provider,
                until.isoformat(),
            )
            mark_provider_sleeping(
                telegram_state,
                provider=exc.provider,
                until=until,
                reason=exc.raw_message or "quota exhausted",
            )
            session = brainstorm_session_active(telegram_state) or {}
            session["paused_quota"] = {
                "provider": exc.provider,
                "until": until.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat(),
                "paused_at": utc_now_iso(),
            }
            telegram_state.brainstorming = session
            until_local = until.astimezone().strftime("%H:%M")
            duration = format_sleep_duration(until)
            send_service_message(
                bot,
                (
                    f"[multicoders-service] Brainstorming pausado: {exc.provider} sin tokens hasta {until_local} ({duration}). "
                    f"Retomo solo cuando vuelva. /brainstorming-cancel para abortar."
                ),
                dry_run,
                run_id=str(session.get("run_id") or "") or None,
            )
        except Exception as exc:
            LOGGER.exception("brainstorming step failed")
            log_exception_details(exc)
            run_id = None
            session = brainstorm_session_active(telegram_state)
            if session is not None:
                run_id = str(session.get("run_id") or "") or None
            telegram_state.brainstorming = None
            send_service_message(
                bot,
                f"[multicoders-service] Corté el brainstorming por un error.\ndetails: {short_exception_text(exc)}",
                dry_run,
                run_id=run_id,
            )
    if not autonomous_chat_changed and not saw_bot_update_in_scope and active_bot_chat(telegram_state) is not None:
        try:
            advance_autonomous_bot_chat(
                state=telegram_state,
                agents=agents,
                repo=conversation_repo,
                timeout_sec=provider_timeout_sec,
                dry_run=dry_run,
                observer_bot=bot,
                conn=conn,
            )
        except Exception as exc:
            LOGGER.exception("autonomous bot chat turn failed")
            log_exception_details(exc)
            state_run_id = None
            chat = active_bot_chat(telegram_state)
            if chat is not None:
                state_run_id = str(chat.get("run_id") or "") or None
            telegram_state.bot_chat = None
            send_service_message(
                bot,
                f"[multicoders-service] Corté la conversación autónoma por un error.\ndetails: {short_exception_text(exc)}",
                dry_run,
                run_id=state_run_id,
            )
    save_telegram_state(telegram_state)


def run_service(args: argparse.Namespace) -> int:
    prepare_provider_args(args)
    configure_logging(level_name=args.log_level, log_file=args.log_file)
    global CHAT_PROVIDER_TIMEOUT_SEC, PROVIDER_COOLDOWN_SEC
    CHAT_PROVIDER_TIMEOUT_SEC = args.chat_provider_timeout_sec
    PROVIDER_COOLDOWN_SEC = args.provider_cooldown_sec
    agents = build_agent_configs(args)
    observer_bot = select_observer_bot(agents)
    db_file = Path(args.db_file).expanduser().resolve() if args.db_file else Path.cwd() / ".multicoders-service" / "service.db"
    telegram_state_file = Path(args.telegram_state_file).expanduser().resolve() if args.telegram_state_file else db_file.parent / "telegram-service-state.json"
    conn = connect_db(db_file)
    init_db(conn)
    telegram_state = load_telegram_state(telegram_state_file)
    LOGGER.info("service started db=%s telegram_state=%s poll_sec=%s dry_run=%s", db_file, telegram_state_file, args.poll_sec, args.dry_run)

    while True:
        LOGGER.debug("service poll cycle started")
        process_service_commands(
            bot=observer_bot,
            conn=conn,
            telegram_state=telegram_state,
            dry_run=args.dry_run,
            providers=list(args.providers),
            agents=agents,
            provider_timeout_sec=args.provider_timeout_sec,
            conversation_repo=Path.cwd(),
        )
        task = claim_next_approved_task(conn, updated_at=utc_now_iso())
        if task is not None:
            args.lead_provider = task.lead_provider
            LOGGER.info("task claimed task_id=%s repo=%s type=%s", task.id, task.repo_path, task.task_type)
            send_service_message(
                observer_bot,
                format_service_event(
                    event="running",
                    task_id=task.id,
                    status="running",
                    repo_path=task.repo_path,
                    task_type=task.task_type,
                    task_text=task.task_text,
                    lead_provider=task.lead_provider,
                ),
                args.dry_run,
                run_id=f"task-{task.id}",
            )
            try:
                LOGGER.info("task execution started task_id=%s", task.id)
                result = execute_council_session(args, repo=Path(task.repo_path), task=task.task_text, task_type=task.task_type)
            except ProviderQuotaError as exc:
                until = exc.retry_at or (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=PROVIDER_COOLDOWN_SEC))
                LOGGER.warning(
                    "task paused by quota task_id=%s provider=%s until=%s",
                    task.id,
                    exc.provider,
                    until.isoformat(),
                )
                mark_provider_sleeping(
                    telegram_state,
                    provider=exc.provider,
                    until=until,
                    reason=exc.raw_message or "quota exhausted",
                )
                save_telegram_state(telegram_state)
                paused_payload = {
                    "paused_quota": {
                        "paused_provider": exc.provider,
                        "until": until.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat(),
                        "reason": (exc.raw_message or "")[:500],
                        "paused_at": utc_now_iso(),
                    }
                }
                update_task_status(
                    conn,
                    task_id=task.id,
                    status="paused_quota",
                    updated_at=utc_now_iso(),
                    result_json=json.dumps(paused_payload, ensure_ascii=False),
                )
                until_local = until.astimezone().strftime("%H:%M")
                duration = format_sleep_duration(until)
                send_service_message(
                    observer_bot,
                    (
                        f"[multicoders-service] Sesión #{task.id} pausada: {exc.provider} sin tokens hasta {until_local} ({duration}). "
                        f"/resume {task.id} cuando vuelva, /cancel {task.id} para descartar, /promote <provider> {task.id} para que un sobreviviente cierre la decisión."
                    ),
                    args.dry_run,
                    run_id=f"task-{task.id}",
                )
            except Exception as exc:
                LOGGER.exception("task execution failed task_id=%s", task.id)
                update_task_status(conn, task_id=task.id, status="failed", updated_at=utc_now_iso(), result_json=json.dumps({"error": str(exc)}))
                send_service_message(
                    observer_bot,
                    format_service_event(event="failed", task_id=task.id, status="failed", details=str(exc)),
                    args.dry_run,
                    run_id=f"task-{task.id}",
                )
            else:
                final_status = "done" if result.get("validation_ok", True) else "failed"
                update_task_status(
                    conn,
                    task_id=task.id,
                    status=final_status,
                    updated_at=utc_now_iso(),
                    run_id=str(result.get("run_id") or ""),
                    result_json=json.dumps(result, ensure_ascii=False),
                )
                LOGGER.info("task execution finished task_id=%s status=%s winner=%s", task.id, final_status, result.get("winner"))
                send_service_message(
                    observer_bot,
                    format_service_event(
                        event=final_status,
                        task_id=task.id,
                        status=final_status,
                        winner=str(result.get("winner") or ""),
                        details=None if final_status == "done" else "validation failed",
                    ),
                    args.dry_run,
                    run_id=str(result.get("run_id") or f"task-{task.id}"),
                )
        else:
            LOGGER.debug("service idle no approved tasks")
        if args.once:
            LOGGER.info("service exiting after single cycle")
            return 0
        import time

        time.sleep(max(args.poll_sec, 1))


def run_send_test_messages(args: argparse.Namespace) -> int:
    prepare_telegram_provider_args(args)
    agents = build_agent_configs(args)
    missing = [agent.provider for agent in agents if agent.bot is None]
    if missing:
        raise SystemExit(
            "Telegram bot configuration is required for send-test-messages. Missing: "
            + ", ".join(missing)
        )

    run_id = f"test-{uuid.uuid4().hex[:8]}"
    planned: list[dict[str, str]] = []
    for index, agent in enumerate(agents, start=1):
        text = f"[multicoders-test] #{index} {agent.display_name}: {args.message}"
        planned.append(
            {
                "provider": agent.provider,
                "display_name": agent.display_name,
                "chat_id": agent.bot.chat_id if agent.bot else "",
                "message_thread_id": str(agent.bot.message_thread_id) if agent.bot and agent.bot.message_thread_id is not None else "",
                "text": annotate_message(text, run_id),
            }
        )
        send_group_message(agent, text, args.dry_run, run_id=run_id)

    print_json("test_messages", planned)
    print(f"test_run_id: {run_id}")
    return 0


def run_discover_telegram_chat(args: argparse.Namespace) -> int:
    prepare_telegram_provider_args(args)
    agents = build_agent_configs(args)
    usable_agents = [agent for agent in agents if agent.bot is not None]
    if not usable_agents:
        raise SystemExit("Telegram bot configuration is required for discover-telegram-chat")

    def dry_run_updates_for(provider: str) -> list[TelegramMessage]:
        if provider == "codex":
            return [
                TelegramMessage(
                    update_id=101,
                    chat_id="-1001234567890",
                    chat_type="supergroup",
                    chat_title="multicoders lab",
                    sender_id="1",
                    sender_name="alice",
                    is_bot=False,
                    text="/status",
                    date=0,
                    message_thread_id=77,
                ),
                TelegramMessage(
                    update_id=102,
                    chat_id="-1001234567890",
                    chat_type="supergroup",
                    chat_title="multicoders lab",
                    sender_id="2",
                    sender_name="bob",
                    is_bot=False,
                    text="plain group message",
                    date=0,
                    message_thread_id=None,
                ),
            ]
        return [
            TelegramMessage(
                update_id=200 + len(provider),
                chat_id="-1001234567890",
                chat_type="supergroup",
                chat_title="multicoders lab",
                sender_id=str(10 + len(provider)),
                sender_name=f"{provider}-user",
                is_bot=False,
                text=f"/status@{provider}",
                date=0,
                message_thread_id=None,
            )
        ]

    per_provider: list[dict[str, object]] = []
    combined: dict[tuple[str, int | None], dict[str, object]] = {}
    for agent in usable_agents:
        updates = dry_run_updates_for(agent.provider) if args.dry_run else agent.bot.get_updates(timeout_sec=0)
        recent_updates = updates[-max(args.limit, 1) :]
        grouped: dict[tuple[str, int | None], dict[str, object]] = {}
        for item in recent_updates:
            key = (item.chat_id, item.message_thread_id)
            bucket = grouped.setdefault(
                key,
                {
                    "chat_id": item.chat_id,
                    "chat_type": item.chat_type,
                    "chat_title": item.chat_title,
                    "message_thread_id": item.message_thread_id,
                    "sample_senders": [],
                    "sample_texts": [],
                    "last_update_id": item.update_id,
                },
            )
            if item.sender_name not in bucket["sample_senders"]:
                bucket["sample_senders"].append(item.sender_name)
            if item.text and len(bucket["sample_texts"]) < 3:
                bucket["sample_texts"].append(item.text)
            bucket["last_update_id"] = max(int(bucket["last_update_id"]), item.update_id)

            combined_bucket = combined.setdefault(
                key,
                {
                    "chat_id": item.chat_id,
                    "chat_type": item.chat_type,
                    "chat_title": item.chat_title,
                    "message_thread_id": item.message_thread_id,
                    "seen_by": [],
                    "sample_texts": [],
                },
            )
            if agent.provider not in combined_bucket["seen_by"]:
                combined_bucket["seen_by"].append(agent.provider)
            if item.text and len(combined_bucket["sample_texts"]) < 3:
                combined_bucket["sample_texts"].append(item.text)

        per_provider.append(
            {
                "provider": agent.provider,
                "display_name": agent.display_name,
                "candidates": list(grouped.values()),
            }
        )

    combined_candidates = list(combined.values())
    result = {
        "providers_checked": [item["provider"] for item in per_provider],
        "per_provider": per_provider,
        "combined_candidates": combined_candidates,
        "recommended_env": [
            {
                "TELEGRAM_GROUP": item["chat_id"],
                "TELEGRAM_TOPIC_ID": item["message_thread_id"] if item["message_thread_id"] is not None else "",
                "SEEN_BY": ",".join(item["seen_by"]),
            }
            for item in combined_candidates
        ],
    }
    print_json("telegram_discovery", result)
    return 0


def run_brainstorming(args: argparse.Namespace) -> int:
    prepare_provider_args(args)
    configure_logging(level_name=args.log_level, log_file=args.log_file)
    context = build_repo_context(Path(args.repo).expanduser().resolve(), allow_non_git=args.dry_run)
    agents = build_agent_configs(args)
    state_file = context.repo / ".multicoders" / "brainstorming-state.json"
    telegram_state = load_telegram_state(state_file)
    telegram_state.state_file = state_file
    if not args.dry_run and not args.no_telegram:
        missing = [agent.provider for agent in agents if agent.bot is None]
        if missing:
            raise MulticodersError(
                "Telegram is required in real mode when mirroring brainstorming. Missing bot configuration for: "
                + ", ".join(missing)
            )
    start_brainstorming_session(
        state=telegram_state,
        topic=args.topic,
        sender_name="cli",
        repo=context.repo,
        dry_run=args.dry_run,
        docs_dir=args.docs_dir,
    )
    save_telegram_state(telegram_state)

    max_steps = 24
    for _ in range(max_steps):
        session = brainstorm_session_active(telegram_state)
        if session is None:
            break
        run_brainstorming_step(
            state=telegram_state,
            agents=agents,
            context=context,
            dry_run=args.dry_run,
            timeout_sec=args.provider_timeout_sec,
            observer_bot=select_observer_bot(agents),
        )
        save_telegram_state(telegram_state)

    session = telegram_state.brainstorming or {}
    if session.get("status") != "completed":
        raise MulticodersError("brainstorming did not finish within the expected number of steps")

    spec_path = session.get("spec_path")
    if isinstance(spec_path, str) and spec_path:
        print(f"spec_path: {spec_path}")
    print_json(
        "brainstorming",
        {
            "run_id": session.get("run_id", ""),
            "topic": session.get("topic", ""),
            "status": session.get("status", ""),
            "selected_proposal_id": session.get("selected_proposal_id", ""),
            "selected_improvement_id": session.get("selected_improvement_id", ""),
        },
    )
    return 0


def _main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "service":
        return run_service(args)
    if args.command == "send-test-messages":
        return run_send_test_messages(args)
    if args.command == "discover-telegram-chat":
        return run_discover_telegram_chat(args)
    if args.command == "brainstorming":
        return run_brainstorming(args)
    prepare_provider_args(args)
    result = execute_council_session(
        args,
        repo=Path(args.repo).expanduser().resolve(),
        task=args.task,
        task_type=args.task_type,
    )
    print_json("specs", result["specs"])
    print_json("reviews", result["reviews"])
    print_json("human_feedback", result["human_feedback"])
    print_json("votes", result["votes"])
    print_json("implementation", result["implementation"])
    print_json("telegram_state", result["telegram_state"])
    print(f"winner: {result['winner']}")
    print(f"validation_ok: {result['validation_ok']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except MulticodersError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
