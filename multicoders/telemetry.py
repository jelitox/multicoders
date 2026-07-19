"""Multicoders Telemetry: Token tracking and performance monitoring.

This module provides utilities to track execution time and estimated
resource usage for each node in the Multicoders flow.
"""
from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

logger = logging.getLogger("multicoders.telemetry")

@dataclass
class NodeMetrics:
    """Metrics for a single flow node execution."""
    node_name: str
    start_time: float
    end_time: Optional[float] = None
    tokens_in: int = 0
    tokens_out: int = 0
    status: str = "pending"

    @property
    def duration(self) -> float:
        if self.end_time:
            return self.end_time - self.start_time
        return time.time() - self.start_time

class Telemetry:
    """Telemetry collector for the Multicoders flow."""

    def __init__(self) -> None:
        self.metrics: Dict[str, List[NodeMetrics]] = {}
        self._current_task: Optional[str] = None

    def start_task(self, task_id: str) -> None:
        self._current_task = task_id
        self.metrics[task_id] = []
        logger.info(f"Telemetry started for task: {task_id}")

    def start_node(self, node_name: str) -> NodeMetrics:
        metric = NodeMetrics(node_name=node_name, start_time=time.time())
        if self._current_task:
            self.metrics[self._current_task].append(metric)
        return metric

    def stop_node(self, metric: NodeMetrics, status: str = "completed", tokens_in: int = 0, tokens_out: int = 0) -> None:
        metric.end_time = time.time()
        metric.status = status
        metric.tokens_in = tokens_in
        metric.tokens_out = tokens_out
        logger.debug(f"Node {metric.node_name} finished in {metric.duration:.2f}s")

    def get_summary(self, task_id: str) -> Dict[str, Any]:
        task_metrics = self.metrics.get(task_id, [])
        total_duration = sum(m.duration for m in task_metrics)
        total_tokens = sum(m.tokens_in + m.tokens_out for m in task_metrics)

        return {
            "task_id": task_id,
            "total_duration_s": round(total_duration, 2),
            "total_tokens": total_tokens,
            "nodes": [
                {
                    "name": m.node_name,
                    "duration": round(m.duration, 2),
                    "status": m.status
                } for m in task_metrics
            ]
        }
