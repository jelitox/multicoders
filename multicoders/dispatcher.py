"""Dispatcher: produces N candidate artifacts per task using real Parrot Agents.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Dict, List, Optional

from parrot.bots.agent import BasicAgent
from .storage import Storage


@dataclass
class Candidate:
    artifact_id: int
    author: str
    content: str


class ParrotCoder:
    def __init__(self, name: str, role: str):
        self.name = name
        self.agent = BasicAgent(
            name=name,
            agent_id=name.lower(),
            system_prompt=f"You are {name}, a expert {role}. Output ONLY clean Python code, no markdown blocks, no explanations."
        )

    async def generate(self, prompt: str) -> str:
        response = await self.agent.ask(prompt)
        return str(response.content).strip()


class Dispatcher:
    def __init__(self, storage: Storage, coders: List[ParrotCoder]) -> None:
        if not coders:
            raise ValueError("Dispatcher needs at least one coder")
        self.storage = storage
        self.coders = coders

    def dispatch(self, task_id: str, prompt: str) -> List[Candidate]:
        self.storage.update_task_status(task_id, "in_progress")
        candidates: List[Candidate] = []
        
        # In a real environment we'd use asyncio.gather, but for simplicity
        # and to keep it deterministic in the flow, we run them sequentially.
        # Note: BasicAgent.ask is typically async.
        loop = asyncio.get_event_loop()
        
        for coder in self.coders:
            content = loop.run_until_complete(coder.generate(prompt))
            artifact_id = self.storage.add_artifact(task_id, coder.name, content)
            candidates.append(
                Candidate(artifact_id=artifact_id, author=coder.name, content=content)
            )
        return candidates
