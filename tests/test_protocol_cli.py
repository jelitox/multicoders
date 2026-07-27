from __future__ import annotations

import hashlib
import json

from multicoders.__main__ import main


def test_perspective_protocol_cli_dry_run(tmp_path, capsys):
    content = "context"
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "mode": "research",
                "requirement": "Cross-check this claim",
                "question": "Is it supported?",
                "providers": ["codex", "claude"],
                "minimum_quorum": 2,
                "evidence": [
                    {
                        "locator": "doc",
                        "sha256": hashlib.sha256(content.encode()).hexdigest(),
                        "content": content,
                    }
                ],
            }
        )
    )
    assert main(
        [
            "perspective",
            "--request",
            str(request),
            "--repo",
            str(tmp_path),
            "--dry-run",
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "completed"
    assert result["brief_hash"]


def test_mockup_protocol_cli_dry_run(tmp_path, capsys):
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "request": "Mock a dashboard",
                "from_scratch": True,
                "views": [{"name": "dashboard", "filename": "dashboard.png"}],
            }
        )
    )
    artifacts = tmp_path / "artifacts"
    assert main(
        [
            "mockup",
            "--request",
            str(request),
            "--repo",
            str(tmp_path),
            "--artifact-dir",
            str(artifacts),
            "--dry-run",
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "completed"
    assert (artifacts / "outputs" / "dashboard.png").is_file()
