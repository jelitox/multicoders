import asyncio
import os
import sys
from typing import Dict, Any

# Ensure we can import the local multicoders package
sys.path.append(os.getcwd())

from multicoders.storage import Storage
from multicoders.dispatcher import Dispatcher, ParrotCoder
from multicoders.arena import Arena
from multicoders.parrot_flow import ParrotMulticodersFlow
from multicoders.mocks import MockLLM

async def run_demo():
    print("🦜 Inicializando Multicoders con motor Parrot...")

    # Setup infrastructure
    storage = Storage(":memory:") # Use memory for demo
    storage.init_db()

    # In a real scenario, these would use real LLM keys
    # For the demo, we use the MockLLM to show the flow
    mock_llm = MockLLM()

    # Setup Parrot Agents through the Dispatcher
    coders = [
        ParrotCoder("codex", provider="openai", model="gpt-4", llm=mock_llm),
        ParrotCoder("claude", provider="anthropic", model="claude-3-opus", llm=mock_llm),
        ParrotCoder("gemini", provider="google", model="gemini-1.5-pro", llm=mock_llm)
    ]

    dispatcher = Dispatcher(storage, coders=coders)
    arena = Arena(storage)

    # Initialize the Parrot-native flow
    flow = ParrotMulticodersFlow(
        storage=storage,
        dispatcher=dispatcher,
        arena=arena,
        name="DemoParrotFlow"
    )

    prompt = "Crea una función en Python para calcular la serie de Fibonacci usando recursión con memoization."

    print(f"🚀 Disparando tarea: '{prompt}'")
    result = await flow.run_async(prompt)

    print("\n✅ Flujo completado!")
    print(f"Estado: {result.get('status')}")
    if result.get("status") == "completed":
        print(f"Ganador: {result.get('winner')}")
        print("-" * 40)
        print("Código generado:")
        print(result.get("winner_candidate").code)
        print("-" * 40)
    else:
        print(f"Fallo: {result.get('reason')}")

if __name__ == "__main__":
    asyncio.run(run_demo())
