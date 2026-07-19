
import asyncio
import logging
import sys
from multicoders.factory import create_multicoders_flow
from multicoders.storage import Storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

async def main():
    """
    Main entry point for Multicoders.
    This script initializes the full DAG and runs a sample research/code generation task.
    """
    logger.info("Initializing Multicoders Engine...")

    # Simple storage initialization
    storage = Storage(":memory:")

    # Create the flow using the factory
    flow_orchestrator = create_multicoders_flow(storage=storage)

    # Task definition
    prompt = sys.argv[1] if len(sys.argv) > 1 else "Implement a thread-safe singleton in Python."
    task_id = "task_" + str(hash(prompt) % 10000)

    logger.info(f"Starting task: {task_id} with prompt: '{prompt}'")

    try:
        # Initial state for the Parrot AgentsFlow
        initial_state = {
            "task_id": task_id,
            "prompt": prompt
        }

        # Execute the flow
        result = await flow_orchestrator.run_async(prompt)

        logger.info("--- FLOW COMPLETED SUCCESSFULLY ---")
        logger.info(f"Final Status: {result.get('status')}")
        logger.info(f"Winner: {result.get('winner')}")

    except Exception as e:
        logger.error(f"Flow failed: {str(e)}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    if "--help" in sys.argv:
        print("Usage: python -m multicoders.main [prompt]")
        sys.exit(0)
    asyncio.run(main())
