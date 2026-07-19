"""MulticodersAgent: A Parrot-native Agent that wraps the Multicoders flow.

This allows the Multicoders logic to be used as a tool or a sub-agent
within larger Parrot architectures.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from parrot.bots.agent import BasicAgent
from .factory import create_multicoders_flow

class MulticodersAgent(BasicAgent):
    """An agent that uses the Multicoders flow to solve coding tasks."""

    def __init__(
        self,
        name: str = "MulticodersAgent",
        db_path: str = "multicoders.db",
        **kwargs
    ) -> None:
        super().__init__(name=name, **kwargs)
        self.flow = create_multicoders_flow(db_path=db_path)

    async def run(self, prompt: str, **kwargs) -> Dict[str, Any]:
        """Entry point for the agent, executing the underlying Multicoders flow."""
        logging.info(f"[{self.name}] Starting flow for prompt: {prompt[:50]}...")
        result = await self.flow.run_async(prompt)

        # Format the output for Parrot-style communication
        if result.get("status") == "completed":
            winner = result.get("winner")
            return {
                "content": f"Multicoders successfully completed the task. Winner: {winner}",
                "metadata": result
            }
        else:
            reason = result.get("reason", "unknown error")
            return {
                "content": f"Multicoders failed to complete the task: {reason}",
                "metadata": result
            }

    def execute_sync(self, prompt: str, **kwargs) -> Dict[str, Any]:
        """Synchronous version for non-async callers."""
        import asyncio
        return asyncio.run(self.run(prompt, **kwargs))
