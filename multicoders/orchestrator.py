"""Multicoders Orchestrator: The unified entry point for the entire system.

This module provides a high-level Orchestrator class that simplifies the
execution of the Multicoders DAG, managing storage, flow initialization,
and results processing.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .arena import Arena
from .dispatcher import Dispatcher
from .factory import create_multicoders_flow
from .parrot_flow import ParrotMulticodersFlow
from .storage import Storage
from .telemetry import Telemetry


class Orchestrator:
    """High-level orchestrator for the Multicoders system."""

    def __init__(
        self,
        db_path: str = "multicoders.db",
        log_level: int = logging.INFO
    ) -> None:
        logging.basicConfig(level=log_level)
        self.logger = logging.getLogger("multicoders.orchestrator")

        # Initialize storage
        self.storage = Storage(db_path)
        self.storage.setup()

        # Initialize telemetry
        self.telemetry = Telemetry()

        # Initialize sub-components
        self.dispatcher = Dispatcher(self.storage)
        self.arena = Arena(self.storage)

        # Create the Parrot flow
        self.flow = ParrotMulticodersFlow(
            storage=self.storage,
            dispatcher=self.dispatcher,
            arena=self.arena
        )

    def execute_task(self, prompt: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Runs the entire Multicoders pipeline for a given prompt."""
        import uuid
        task_id = str(uuid.uuid4())
        self.telemetry.start_task(task_id)

        self.logger.info(f"Starting execution for prompt: {prompt[:50]}...")

        try:
            # We wrap the flow run with telemetry if needed, or pass it to the flow
            result = self.flow.run(prompt, payload)

            summary = self.telemetry.get_summary(task_id)
            result["telemetry"] = summary

            if result.get("status") == "completed":
                self.logger.info(f"Task completed successfully in {summary['total_duration_s']}s.")
            else:
                self.logger.error(f"Task failed: {result.get('reason', 'Unknown error')}")

            return result
        except Exception as e:
            self.logger.exception(f"Critical failure in orchestrator: {str(e)}")
            return {"status": "error", "error": str(e)}

    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves the current status and checkpoints for a task."""
        task = self.storage.get_task(task_id)
        if not task:
            return None

        checkpoints = self.storage.get_checkpoints(task_id)
        return {
            "task": task,
            "checkpoints": checkpoints
        }

if __name__ == "__main__":
    # Example usage
    orch = Orchestrator()
    result = orch.execute_task("Implement a thread-safe singleton in Python.")
    print(f"Final Result: {result['status']}")
