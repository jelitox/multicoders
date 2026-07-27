"""Machine-readable CLI for Hornero and other coordinators."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from multicoders.advisory import AdvisoryError, AdvisoryRequest, run_advisory
from multicoders.mockups import MockupError, MockupRequest, run_mockup


def main(command: str, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=f"multicoders {command}")
    parser.add_argument(
        "--request",
        default="-",
        help="request JSON file; '-' reads stdin",
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    if command == "mockup":
        parser.add_argument("--artifact-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        payload = _read_request(args.request)
        if command == "perspective":
            request = AdvisoryRequest.from_dict(payload)
            result = run_advisory(
                request,
                args.repo,
                dry_run=args.dry_run,
                artifact_dir=args.output.parent if args.output else None,
            )
        elif command == "mockup":
            request = MockupRequest.from_dict(payload)
            result = run_mockup(
                request,
                args.repo,
                args.artifact_dir,
                dry_run=args.dry_run,
                allowed_input_roots=(args.repo, args.artifact_dir),
            )
        else:
            raise AssertionError(f"unknown protocol command: {command}")
    except (AdvisoryError, MockupError, OSError, json.JSONDecodeError) as exc:
        print(f"{command} failed: {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result.get("status") == "completed" else 3


def _read_request(value: str) -> dict[str, Any]:
    text = sys.stdin.read() if value == "-" else Path(value).read_text(encoding="utf-8")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise AdvisoryError("request JSON must be an object")
    return payload
