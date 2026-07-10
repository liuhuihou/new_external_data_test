from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class ToolResult:
    ok: bool
    data: dict[str, Any] | None = None
    error: str = ""


class Tool(Protocol):
    name: str

    def run(self, **kwargs: Any) -> ToolResult:
        ...
