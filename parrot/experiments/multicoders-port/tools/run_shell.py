import subprocess
from typing import Any, Dict
from .base import Tool

class RunShellTool:
    name = "run_shell"
    description = "Executes a shell command and returns the output."
    input_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute."
            }
        },
        "required": ["command"]
    }

    async def run(self, command: str) -> str:
        try:
            # Timeout de 30 segundos para evitar procesos zombies
            process = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30
            )
            output = f"STDOUT:\n{process.stdout}\nSTDERR:\n{process.stderr}"
            if process.returncode != 0:
                output = f"Return Code: {process.returncode}\n" + output
            return output
        except subprocess.TimeoutExpired:
            return "Error: Command timed out after 30 seconds."
        except Exception as e:
            return f"Error executing command: {str(e)}"
