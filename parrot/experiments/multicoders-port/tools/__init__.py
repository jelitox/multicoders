from .read_file import ReadFileTool
from .write_file import WriteFileTool
from .run_shell import RunShellTool
from .web_search import WebSearchTool

# Registro de herramientas disponibles
TOOLS = {
    "read_file": ReadFileTool(),
    "write_file": WriteFileTool(),
    "run_shell": RunShellTool(),
    "web_search": WebSearchTool()
}

def get_tool(name: str):
    return TOOLS.get(name)

def list_tools():
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema
        }
        for tool in TOOLS.values()
    ]
