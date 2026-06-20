"""Layered memory for Multicoders (SPEC §3.2).

``MemoryService`` is the facade both engines consume. Fase 4a ships the
working + decision layers (no ai-parrot bump required); Fase 4b plugs in
PageIndex/GraphIndex behind the same layer protocols.
"""
from __future__ import annotations

from .base import (
    Decision,
    DecisionMemoryLayer,
    DocumentMemoryLayer,
    Recall,
    WorkingMemoryLayer,
)
from .decision import JsonDecisionMemory
from .graphindex import GraphIndexEpisodicMemory, graphindex_available
from .pageindex import PageIndexDocumentMemory, pageindex_available
from .service import MemoryService
from .working import InProcessWorkingMemory

__all__ = [
    "MemoryService",
    "Decision",
    "Recall",
    "WorkingMemoryLayer",
    "DecisionMemoryLayer",
    "DocumentMemoryLayer",
    "InProcessWorkingMemory",
    "JsonDecisionMemory",
    "PageIndexDocumentMemory",
    "pageindex_available",
    "GraphIndexEpisodicMemory",
    "graphindex_available",
]
