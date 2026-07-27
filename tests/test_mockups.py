from __future__ import annotations

import json
from pathlib import Path

import pytest

from multicoders.mockups import (
    MockupError,
    MockupRequest,
    _DRY_RUN_PNG,
    inspect_png,
    run_mockup,
)
from multicoders.providers import ProviderResult


def _request(screenshot: Path | None = None, **overrides):
    payload = {
        "request": "Add a scheduling panel",
        "screenshot": str(screenshot) if screenshot else "",
        "from_scratch": screenshot is None,
        "views": [
            {"name": "calendar", "filename": "calendar.png"},
            {"name": "details", "filename": "details.png"},
        ],
        "constraints": [
            {"source": "SPEC-100:L10", "quote": "Keep the sidebar unchanged."}
        ],
        "product_context": "Field service scheduling",
        "visual_language": "Compact cards and a blue accent",
    }
    payload.update(overrides)
    return MockupRequest.from_dict(payload)


def test_dry_run_creates_manifest_and_exact_pngs(tmp_path):
    artifacts = tmp_path / "artifacts"
    result = run_mockup(
        _request(),
        tmp_path,
        artifacts,
        dry_run=True,
        run_id="mockup-test",
    )
    assert result["status"] == "completed"
    assert [item["filename"] for item in result["artifacts"]] == [
        "calendar.png",
        "details.png",
    ]
    assert inspect_png(artifacts / "outputs" / "calendar.png") == (1, 1)
    persisted = json.loads((artifacts / "manifest.json").read_text())
    assert persisted["brief_sha256"] == result["brief_sha256"]
    assert persisted["promoted"] is False


def test_real_adapter_attaches_reference_and_validates_outputs(tmp_path):
    screenshot = tmp_path / "screen.png"
    screenshot.write_bytes(_DRY_RUN_PNG)
    captured = {}

    def runner(**kwargs):
        captured.update(kwargs)
        for filename in ("calendar.png", "details.png"):
            (kwargs["repo"] / "outputs" / filename).write_bytes(_DRY_RUN_PNG)
        return ProviderResult(
            "codex",
            '{"session_id":"session-1","response":"files generated; assumptions: none"}',
            "",
        )

    result = run_mockup(
        _request(screenshot),
        tmp_path,
        tmp_path / "artifacts",
        runner=runner,
    )
    assert result["status"] == "completed"
    assert captured["sandbox_mode"] == "workspace-write"
    assert captured["allow_api_keys"] is False
    assert len(captured["images"]) == 1
    assert result["session_id"] == "session-1"
    assert result["reference"]["sha256"]


def test_mockup_rejects_traversal_filename():
    with pytest.raises(MockupError, match="plain .png"):
        _request(views=[{"name": "bad", "filename": "../bad.png"}])


def test_mockup_rejects_reference_outside_allowed_root(tmp_path):
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(_DRY_RUN_PNG)
    try:
        with pytest.raises(MockupError, match="escapes"):
            run_mockup(
                _request(outside),
                tmp_path,
                tmp_path / "artifacts",
                dry_run=True,
            )
    finally:
        outside.unlink()


def test_mockup_reports_missing_output_instead_of_false_success(tmp_path):
    screenshot = tmp_path / "screen.png"
    screenshot.write_bytes(_DRY_RUN_PNG)

    def runner(**kwargs):
        (kwargs["repo"] / "outputs" / "calendar.png").write_bytes(_DRY_RUN_PNG)
        return ProviderResult("codex", '{"response":"partial"}', "")

    result = run_mockup(
        _request(screenshot),
        tmp_path,
        tmp_path / "artifacts",
        runner=runner,
    )
    assert result["status"] == "failed"
    assert any("details.png" in error for error in result["errors"])
