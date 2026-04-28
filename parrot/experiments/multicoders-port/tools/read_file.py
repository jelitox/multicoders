import os
from typing import Any, Dict
from .base import Tool

class ReadFileTool:
    name = "read_file"
    description = "Reads the content of a file from the filesystem."
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The path to the file to read."
            }
        },
        "required": ["file_path"]
    }

    async def run(self, file_path: str) -> str:
        if not os.path.exists(file_path):
            return f"Error: File '{file_path}' not found."
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            return f"Error reading file: {str(e)}"
