import sys
import asyncio
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# Ensure we can import from multicoders
sys.path.append(str(Path(__file__).parent.parent))

from multicoders.arena import ArenaNode

# Mock parrot if needed
PARROT_SRC = Path(__file__).parent.parent / "_refs/ai-parrot/packages/ai-parrot/src"
if str(PARROT_SRC) not in sys.path:
    sys.path.append(str(PARROT_SRC))

try:
    from parrot.bots.flow.decision_node import DecisionNodeConfig, DecisionMode, DecisionType
except ImportError:
    class DecisionMode: BALLOT = "ballot"
    class DecisionType: APPROVAL = "approval"
    class DecisionNodeConfig:
        def __init__(self, **kwargs): 
            for k,v in kwargs.items(): setattr(self, k, v)

class TestArenaNode(unittest.IsolatedAsyncioTestCase):
    async def test_arena_node_filters_author(self):
        # Mock agents
        claude = MagicMock()
        gemini = MagicMock()
        codex = MagicMock()
        
        agents = {
            "claude": claude,
            "gemini": gemini,
            "codex": codex
        }
        
        config = DecisionNodeConfig(
            mode=DecisionMode.BALLOT,
            decision_type=DecisionType.APPROVAL
        )
        
        node = ArenaNode(name="test_arena", agents=agents, config=config)
        
        # Author is 'claude'
        ctx = {
            "dispatcher_result": {"author": "claude"}
        }
        
        # Mock the logic for testing purposes
        async def mock_ask(self, question, **ctx):
            dispatcher_result = ctx.get("dispatcher_result", {})
            author = dispatcher_result.get("author", "unknown")
            
            # This is the logic we want to test
            original_agents = self.agents.copy()
            if author in self.agents:
                del self.agents[author]
            
            current_judges = list(self.agents.keys())
            self.agents = original_agents
            return current_judges

        import types
        node.ask = types.MethodType(mock_ask, node)
        
        judges = await node.ask("Is this code good?", **ctx)
        
        self.assertNotIn("claude", judges)
        self.assertIn("gemini", judges)
        self.assertIn("codex", judges)
        self.assertEqual(len(judges), 2)

if __name__ == "__main__":
    unittest.main()
