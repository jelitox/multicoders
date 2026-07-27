"""Grounded raster mockup generation through a provider CLI."""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import struct
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from multicoders.advisory import ConstraintItem
from multicoders.providers import ProviderError, ProviderResult, run_provider

MOCKUP_PROTOCOL_VERSION = 1
MAX_REFERENCE_BYTES = 20 * 1024 * 1024
MAX_OUTPUT_BYTES = 30 * 1024 * 1024
MAX_VIEWS = 8
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SIGNATURE = b"\xff\xd8\xff"
_DRY_RUN_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNg"
    "YAAAAAMAASsJTYQAAAAASUVORK5CYII="
)


class MockupError(ValueError):
    """A mockup request or generated artifact is invalid."""


@dataclass(frozen=True, slots=True)
class MockupView:
    name: str
    filename: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MockupView":
        view = cls(
            name=_required_text(raw, "name"),
            filename=_required_text(raw, "filename"),
        )
        view.validate()
        return view

    def validate(self) -> None:
        path = Path(self.filename)
        if (
            path.name != self.filename
            or path.suffix.lower() != ".png"
            or self.filename in {".png", ".."}
        ):
            raise MockupError(
                f"view filename must be a plain .png name: {self.filename}"
            )


@dataclass(frozen=True, slots=True)
class MockupRequest:
    request: str
    screenshot: Path | None
    views: tuple[MockupView, ...]
    constraints: tuple[ConstraintItem, ...] = ()
    product_context: str = ""
    visual_language: str = ""
    provider: str = "codex"
    model: str | None = None
    timeout_sec: int = 900
    autonomous: bool = False
    from_scratch: bool = False
    resume: bool = False
    parent_run_id: str | None = None
    protocol_version: int = MOCKUP_PROTOCOL_VERSION

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MockupRequest":
        screenshot_raw = str(raw.get("screenshot") or "")
        request = cls(
            request=_required_text(raw, "request"),
            screenshot=Path(screenshot_raw) if screenshot_raw else None,
            views=tuple(
                MockupView.from_dict(_mapping(item, "view"))
                for item in _list(raw.get("views"), "views")
            ),
            constraints=tuple(
                ConstraintItem.from_dict(_mapping(item, "constraint"))
                for item in _list(raw.get("constraints", []), "constraints")
            ),
            product_context=str(raw.get("product_context") or ""),
            visual_language=str(raw.get("visual_language") or ""),
            provider=str(raw.get("provider") or "codex"),
            model=str(raw["model"]) if raw.get("model") else None,
            timeout_sec=_positive_int(raw.get("timeout_sec", 900), "timeout_sec"),
            autonomous=bool(raw.get("autonomous", False)),
            from_scratch=bool(raw.get("from_scratch", False)),
            resume=bool(raw.get("resume", False)),
            parent_run_id=(
                str(raw["parent_run_id"]) if raw.get("parent_run_id") else None
            ),
            protocol_version=int(
                raw.get("protocol_version", MOCKUP_PROTOCOL_VERSION)
            ),
        )
        request.validate()
        return request

    def validate(self) -> None:
        if self.protocol_version != MOCKUP_PROTOCOL_VERSION:
            raise MockupError(
                f"unsupported mockup protocol version: {self.protocol_version}"
            )
        if self.provider != "codex":
            raise MockupError("the current image artifact adapter requires codex")
        if not self.views or len(self.views) > MAX_VIEWS:
            raise MockupError(f"views must contain between 1 and {MAX_VIEWS} items")
        filenames = [view.filename for view in self.views]
        if len(filenames) != len(set(filenames)):
            raise MockupError("view filenames must be unique")
        if self.from_scratch and self.screenshot is not None:
            raise MockupError("from_scratch cannot include a screenshot")
        if not self.from_scratch and self.screenshot is None:
            raise MockupError("a screenshot is required unless from_scratch is true")


ProviderRunner = Callable[..., ProviderResult]


def run_mockup(
    request: MockupRequest,
    repo: Path,
    artifact_dir: Path,
    *,
    runner: ProviderRunner = run_provider,
    dry_run: bool = False,
    run_id: str | None = None,
    allowed_input_roots: tuple[Path, ...] | None = None,
    clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    request.validate()
    repo = repo.resolve()
    if not repo.is_dir():
        raise MockupError(f"repository does not exist: {repo}")
    run_ref = run_id or f"mockup-{uuid.uuid4().hex[:12]}"
    artifact_dir = artifact_dir.resolve()
    references_dir = artifact_dir / "references"
    outputs_dir = artifact_dir / "outputs"
    references_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)

    reference: dict[str, Any] | None = None
    attached_images: tuple[Path, ...] = ()
    if not request.from_scratch:
        assert request.screenshot is not None
        roots = tuple(root.resolve() for root in (allowed_input_roots or (repo,)))
        screenshot = request.screenshot.resolve()
        if not any(_is_within(screenshot, root) for root in roots):
            raise MockupError("screenshot escapes the allowed input roots")
        media_type, dimensions = inspect_reference_image(screenshot)
        copied_reference = references_dir / (
            "reference" + (".png" if media_type == "image/png" else ".jpg")
        )
        shutil.copyfile(screenshot, copied_reference)
        reference = {
            "path": str(copied_reference),
            "source_path": str(screenshot),
            "sha256": _sha256(copied_reference),
            "media_type": media_type,
            "width": dimensions[0],
            "height": dimensions[1],
            "bytes": copied_reference.stat().st_size,
        }
        attached_images = (copied_reference,)

    brief = {
        "protocol_version": request.protocol_version,
        "request": request.request,
        "reference": reference,
        "views": [asdict(view) for view in request.views],
        "constraints": [asdict(item) for item in request.constraints],
        "product_context": request.product_context,
        "visual_language": request.visual_language,
        "rules": [
            "Use the attached screenshot as the edit target when present.",
            "Keep surrounding application chrome untouched.",
            "Create exactly one PNG per named view using the exact filename.",
            "Treat decided constraints as hard requirements.",
            "Use domain-realistic placeholder content.",
            "List files, assumptions and open questions in the final response.",
        ],
    }
    brief_path = artifact_dir / "brief.json"
    brief_path.write_text(
        json.dumps(brief, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    provider_report = artifact_dir / "provider-result.txt"
    prompt = _mockup_prompt(brief, outputs_dir)
    started_at = clock()
    if dry_run:
        for view in request.views:
            (outputs_dir / view.filename).write_bytes(_DRY_RUN_PNG)
        provider_result = ProviderResult(
            request.provider,
            json.dumps(
                {
                    "session_id": f"dry-{run_ref}",
                    "response": (
                        "Generated: "
                        + ", ".join(view.filename for view in request.views)
                        + "\nAssumptions: dry-run\nOpen questions: none"
                    ),
                }
            ),
            "",
        )
        provider_report.write_text(
            provider_result.text_output() + "\n", encoding="utf-8"
        )
    else:
        try:
            provider_result = runner(
                provider_name=request.provider,
                prompt=prompt,
                repo=artifact_dir,
                model=request.model,
                timeout_sec=request.timeout_sec,
                sandbox_mode="workspace-write",
                images=attached_images,
                output_file=provider_report,
                resume=request.resume,
                allow_api_keys=False,
            )
        except ProviderError as exc:
            return _failed_manifest(
                artifact_dir,
                run_ref,
                request,
                brief,
                reference,
                started_at,
                clock(),
                str(exc),
            )
        if not provider_report.exists():
            provider_report.write_text(
                provider_result.text_output() + "\n", encoding="utf-8"
            )

    artifacts: list[dict[str, Any]] = []
    errors: list[str] = []
    expected = {view.filename for view in request.views}
    unexpected = sorted(
        path.name
        for path in outputs_dir.iterdir()
        if path.is_file() and path.name not in expected
    )
    if unexpected:
        errors.append(f"undeclared output files: {', '.join(unexpected)}")
    for view in request.views:
        path = outputs_dir / view.filename
        try:
            width, height = inspect_png(path)
        except MockupError as exc:
            errors.append(str(exc))
            continue
        artifacts.append(
            {
                "view": view.name,
                "filename": view.filename,
                "path": str(path),
                "sha256": _sha256(path),
                "media_type": "image/png",
                "width": width,
                "height": height,
                "bytes": path.stat().st_size,
            }
        )

    report_text = provider_report.read_text(
        encoding="utf-8", errors="replace"
    )
    if not report_text.strip():
        errors.append("provider result report is empty")
    status = "completed" if not errors and len(artifacts) == len(request.views) else "failed"
    manifest = {
        "protocol_version": MOCKUP_PROTOCOL_VERSION,
        "run_id": run_ref,
        "parent_run_id": request.parent_run_id,
        "status": status,
        "provider": request.provider,
        "session_id": _extract_session_id(provider_result.stdout),
        "started_at": started_at,
        "finished_at": clock(),
        "brief_sha256": _sha256(brief_path),
        "reference": reference,
        "artifacts": artifacts,
        "provider_report": str(provider_report),
        "assumptions_and_questions": report_text.strip(),
        "errors": errors,
        "autonomous": request.autonomous,
        "promoted": False,
    }
    _write_manifest(artifact_dir, manifest)
    return manifest


def inspect_reference_image(path: Path) -> tuple[str, tuple[int, int]]:
    if not path.is_file() or path.is_symlink():
        raise MockupError(f"reference image is not a regular file: {path}")
    size = path.stat().st_size
    if size <= 0 or size > MAX_REFERENCE_BYTES:
        raise MockupError(f"reference image size is outside limits: {path}")
    header = path.read_bytes()[:32]
    if header.startswith(_PNG_SIGNATURE):
        return "image/png", inspect_png(path)
    if header.startswith(_JPEG_SIGNATURE):
        return "image/jpeg", _inspect_jpeg(path)
    raise MockupError(f"unsupported reference image type: {path}")


def inspect_png(path: Path) -> tuple[int, int]:
    if not path.is_file() or path.is_symlink():
        raise MockupError(f"missing PNG output: {path.name}")
    size = path.stat().st_size
    if size <= 0 or size > MAX_OUTPUT_BYTES:
        raise MockupError(f"PNG output size is outside limits: {path.name}")
    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) < 24 or not header.startswith(_PNG_SIGNATURE):
        raise MockupError(f"invalid PNG output: {path.name}")
    width, height = struct.unpack(">II", header[16:24])
    if width <= 0 or height <= 0 or width * height > 100_000_000:
        raise MockupError(f"PNG dimensions are outside limits: {path.name}")
    return width, height


def _inspect_jpeg(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        index += 2
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(data):
            break
        length = int.from_bytes(data[index : index + 2], "big")
        if marker in range(0xC0, 0xC4) and index + 7 <= len(data):
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            if width > 0 and height > 0:
                return width, height
        if length < 2:
            break
        index += length
    raise MockupError(f"invalid JPEG reference: {path}")


def _mockup_prompt(brief: dict[str, Any], outputs_dir: Path) -> str:
    return (
        "Use the installed image generation skill to create raster product "
        "mockups. Save every requested PNG directly in this writable directory: "
        f"{outputs_dir}. Do not edit repository source files. Follow the JSON "
        "brief exactly and preserve surrounding app chrome. In the final message "
        "list generated files, assumptions and open questions.\n\nMOCKUP_BRIEF_JSON:\n"
        + json.dumps(brief, sort_keys=True, ensure_ascii=False)
    )


def _failed_manifest(
    artifact_dir: Path,
    run_id: str,
    request: MockupRequest,
    brief: dict[str, Any],
    reference: dict[str, Any] | None,
    started_at: float,
    finished_at: float,
    error: str,
) -> dict[str, Any]:
    manifest = {
        "protocol_version": MOCKUP_PROTOCOL_VERSION,
        "run_id": run_id,
        "parent_run_id": request.parent_run_id,
        "status": "failed",
        "provider": request.provider,
        "session_id": None,
        "started_at": started_at,
        "finished_at": finished_at,
        "brief_sha256": hashlib.sha256(
            json.dumps(brief, sort_keys=True).encode()
        ).hexdigest(),
        "reference": reference,
        "artifacts": [],
        "provider_report": str(artifact_dir / "provider-result.txt"),
        "assumptions_and_questions": "",
        "errors": [error],
        "autonomous": request.autonomous,
        "promoted": False,
    }
    _write_manifest(artifact_dir, manifest)
    return manifest


def _write_manifest(artifact_dir: Path, manifest: dict[str, Any]) -> None:
    (artifact_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _extract_session_id(stdout: str) -> str | None:
    try:
        payload = json.loads(stdout.strip())
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict) and isinstance(payload.get("session_id"), str):
        return str(payload["session_id"])
    return None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _required_text(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MockupError(f"{key} must be a non-empty string")
    return value


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MockupError(f"{field_name} must be a positive integer")
    return value


def _list(value: Any, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        raise MockupError(f"{field_name} must be a list")
    return value


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MockupError(f"{field_name} must be an object")
    return value
