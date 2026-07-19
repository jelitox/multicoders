"""Integration with ai-parrot-tools for real-world side effects.

This module demonstrates how to use the GitToolkit from parrot_tools
to turn Multicoders winners into actual commits or PRs.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Handle parrot_tools discovery if not in environment
def _setup_paths():
    repo_root = Path(__file__).resolve().parents[1]
    tools_src = repo_root / "_refs/ai-parrot/packages/ai-parrot-tools/src"
    if tools_src.exists() and str(tools_src) not in sys.path:
        sys.path.insert(0, str(tools_src))

_setup_paths()

try:
    from parrot_tools.gittoolkit import GitToolkit, GitPatchFile
except ImportError:
    GitToolkit = None

class GitIntegration:
    def __init__(self, repo_path: str, github_token: Optional[str] = None):
        self.repo_path = repo_path
        if GitToolkit:
            self.toolkit = GitToolkit(
                github_token=github_token,
                default_repository=repo_path # e.g. "owner/repo"
            )
        else:
            self.toolkit = None

    async def commit_winner(self, file_path: str, content: str, message: str):
        """Uses GitToolkit to simulate or perform a commit."""
        if not self.toolkit:
            print(f"[GitIntegration] GitToolkit not available. Simulation: Commit '{message}' to {file_path}")
            return

        # In a real scenario, we would use toolkit.generate_git_apply_patch
        # or toolkit.create_pull_request.
        print(f"[GitIntegration] Using Parrot GitToolkit to process winner for {file_path}")
        # Implementation details depend on the specific GitToolkit version
        # but here we show the intent.

def show_available_tools():
    """Prints all tools registered in the ai-parrot-tools registry."""
    try:
        from parrot_tools import TOOL_REGISTRY
        print("Available Parrot Tools:")
        for name, path in TOOL_REGISTRY.items():
            print(f"- {name}: {path}")
    except ImportError:
        print("ai-parrot-tools registry not found.")

if __name__ == "__main__":
    show_available_tools()
