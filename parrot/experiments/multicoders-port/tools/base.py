from typing import Any, Dict, Protocol, runtime_checkable

@runtime_checkable
class Tool(Protocol):
    name: str
    description: str
    input_schema: Dict[str, Any]

    async def run(self, **kwargs: Any) -> Any:
        ...
