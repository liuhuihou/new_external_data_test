from __future__ import annotations

from typing import Any

from tools.base import ToolResult


class GenerateReportTool:
    name = "generate_report"

    def __init__(self, backend: Any):
        self.backend = backend

    def run(self, *, analysis: dict[str, Any]) -> ToolResult:
        try:
            report = self.backend.build_report(analysis)
            return ToolResult(ok=True, data={"report": report})
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, error=str(exc))
