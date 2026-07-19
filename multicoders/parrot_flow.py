"""Parrot-native orchestration using AgentsFlow.

This module provides a production-ready implementation of the Multicoders
DAG using ai-parrot's FlowNode and AgentsFlow abstractions.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from typing import Any, Dict, List, Optional, Union

from .arena import Arena, ArenaVerdict
from .dispatcher import Candidate, Dispatcher, ParrotCoder
from .qa import QANode, QAReport
from .research import ResearchNode
from .storage import Storage
from .tools_integration import GitIntegration


class ParrotMulticodersFlow:
    """Orchestrator that wraps the Multicoders logic into a Parrot AgentsFlow."""

    def __init__(
        self,
        storage: Storage,
        dispatcher: Dispatcher,
        arena: Arena,
        research_node: Optional[ResearchNode] = None,
        qa_node: Optional[QANode] = None,
        name: str = "MulticodersFlow",
    ) -> None:
        self.storage = storage
        self.dispatcher = dispatcher
        self.arena = arena
        self.research_node = research_node or ResearchNode(storage)
        self.qa_node = qa_node or QANode(storage)
        self.git = GitIntegration(repo_path=".")

        self.name = name
        self.flow = None

    def _setup_flow(self) -> None:
        """Kept for backward compatibility with older callers."""
        return None

    async def _run_research(self, state: Dict[str, Any]) -> Dict[str, Any]:
        task_id = state["task_id"]
        prompt = state["prompt"]
        context = self.research_node.enrich(task_id, prompt)
        self.storage.save_checkpoint(task_id, "research", {"enriched": context.enriched_prompt})
        return {"enriched_prompt": context.enriched_prompt}

    async def _run_dispatch(self, state: Dict[str, Any]) -> Dict[str, Any]:
        task_id = state["task_id"]
        enriched_prompt = state["enriched_prompt"]
        if type(self.dispatcher) is Dispatcher:
            candidates = await self.dispatcher.dispatch_async(task_id, enriched_prompt)
        else:
            candidates = await self._maybe_await(
                self.dispatcher.dispatch(task_id, enriched_prompt)
            )
        self.storage.save_checkpoint(task_id, "dispatcher", {"count": len(candidates)})
        return {"candidates": candidates}

    async def _run_arena(self, state: Dict[str, Any]) -> Dict[str, Any]:
        task_id = state["task_id"]
        candidates = state["candidates"]
        verdicts = await self._maybe_await(self.arena.run(task_id, candidates))
        self.storage.save_checkpoint(task_id, "arena", {"verdicts": len(verdicts)})
        return {"verdicts": verdicts}

    async def _run_qa(self, state: Dict[str, Any]) -> Dict[str, Any]:
        task_id = state["task_id"]
        candidates = state["candidates"]
        verdicts = state["verdicts"]

        winner = None
        for candidate in candidates:
            v = next((v for v in verdicts if v.artifact_id == candidate.artifact_id), None)
            if v and v.status == "approved":
                report = self.qa_node.check(task_id, candidate)
                if report.passed:
                    winner = candidate
                    break

        if winner:
            return {"status": "qa_passed", "winner": winner.author, "winner_candidate": winner}

        self.storage.update_task_status(task_id, "failed")
        return {"status": "failed", "reason": "no candidate passed QA"}

    async def _run_commit(self, state: Dict[str, Any]) -> Dict[str, Any]:
        if state.get("status") != "qa_passed":
            return state

        task_id = state["task_id"]
        winner = state["winner_candidate"]

        # Use Parrot GitToolkit via our integration
        await self.git.commit_winner(
            file_path="multicoders/output.py",
            content=winner.content,
            message=f"feat: solution by {winner.author} for task {task_id}"
        )

        self.storage.update_task_status(task_id, "completed")
        self.storage.save_checkpoint(task_id, "commit", {"artifact_id": winner.artifact_id})

        return {
            "status": "completed",
            "winner": state["winner"],
            "winner_candidate": winner,
            "committed": True
        }

    async def run_async(self, prompt: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        """Execute the flow with the current Parrot-compatible node handlers."""
        task_id = str(uuid.uuid4())
        self.storage.create_task(task_id, payload or {"prompt": prompt})
        state: Dict[str, Any] = {"task_id": task_id, "prompt": prompt}

        for handler in (
            self._run_research,
            self._run_dispatch,
            self._run_arena,
            self._run_qa,
            self._run_commit,
        ):
            update = await handler(state)
            state.update(update)
            if state.get("status") == "failed":
                break
        return state

    def run(self, prompt: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        """Synchronous wrapper for run_async."""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        if loop.is_running():
            # If we are in an environment like a notebook where the loop is already running
            import nest_asyncio
            nest_asyncio.apply()

        return loop.run_until_complete(self.run_async(prompt, payload))

    @staticmethod
    async def _maybe_await(value: Any) -> Any:
        if inspect.isawaitable(value):
            return await value
        return value
