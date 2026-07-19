"""Parrot-Lens: AST-based inspection of the Parrot API.

This module provides tools to scan and extract signatures from the ai-parrot
package, ensuring agents have real API contracts instead of hallucinations.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import List, Optional


class ParrotLens:
    """Scans Python source files to extract class and method signatures."""

    def __init__(self, parrot_path: Optional[Path] = None) -> None:
        if parrot_path:
            self.parrot_path = parrot_path
        else:
            # Auto-discovery
            base = Path(__file__).resolve().parents[1]
            options = [
                base / "_refs" / "ai-parrot" / "packages" / "ai-parrot" / "src" / "parrot",
                base / "_refs" / "ai-parrot" / "packages" / "ai-parrot" / "parrot",
                base / "parrot",
            ]
            self.parrot_path = next((p for p in options if p.exists()), None)

    def get_api_surface(self) -> str:
        """Returns a string representation of the Parrot API surface."""
        if not self.parrot_path or not self.parrot_path.exists():
            return f"Parrot API surface: (path not found in {self.parrot_path})"

        targets = [
            self.parrot_path / "bots" / "agent.py",
            self.parrot_path / "bots" / "basic.py",
            self.parrot_path / "bots" / "abstract.py",
            self.parrot_path / "bots" / "flow" / "fsm.py",
            self.parrot_path / "bots" / "flow" / "node.py",
        ]

        lines: List[str] = [f"## Parrot API Surface ({self.parrot_path})", ""]
        for src in targets:
            if not src.exists():
                continue
            try:
                content = src.read_text()
                tree = ast.parse(content)
                lines.append(f"### File: {src.relative_to(self.parrot_path.parent)}")
                lines.extend(self._parse_tree(tree))
                lines.append("-" * 40)
            except Exception as e:
                lines.append(f"# Error parsing {src.name}: {e}")

            if len(lines) > 200:
                lines.append("... (truncated)")
                break

        return "\n".join(lines)

    def _parse_tree(self, tree: ast.Module) -> List[str]:
        lines: List[str] = []
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                bases = ", ".join(ast.unparse(b) for b in node.bases) or "object"
                lines.append(f"class {node.name}({bases}):")
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        # Skip private methods
                        if item.name.startswith("_") and not item.name.startswith("__"):
                            continue
                        prefix = "async def" if isinstance(item, ast.AsyncFunctionDef) else "def"
                        args = ast.unparse(item.args)
                        lines.append(f"    {prefix} {item.name}({args}): ...")
                lines.append("")
        return lines
