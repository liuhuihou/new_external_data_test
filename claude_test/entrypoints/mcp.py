from __future__ import annotations

import json
import sys
from typing import Any

from entrypoints.sdk import WorkbenchSDK


class WorkbenchMCPServer:
    """Minimal MCP-compatible JSON-RPC bridge."""

    def __init__(self, sdk: WorkbenchSDK | None = None):
        self.sdk = sdk or WorkbenchSDK()

    def tool_specs(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "upload_dataset",
                "description": "Upload a CSV/XLSX file and infer field mapping.",
                "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            },
            {
                "name": "analyze_dataset",
                "description": "Analyze a previously uploaded dataset path.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "mapping": {"type": "object"},
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "generate_report",
                "description": "Generate an AI or local report from a dataset path.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "mapping": {"type": "object"},
                    },
                    "required": ["path"],
                },
            },
        ]

    def _wrap_response(self, response: Any) -> dict[str, Any]:
        if response.ok:
            payload = response.data or {}
            return {
                "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
                "isError": False,
            }
        return {
            "content": [{"type": "text", "text": response.error}],
            "isError": True,
        }

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        path = arguments.get("path", "")
        mapping = arguments.get("mapping")
        if name == "upload_dataset":
            return self._wrap_response(self.sdk.upload_path(path))
        if name == "analyze_dataset":
            return self._wrap_response(self.sdk.analyze_path(path, mapping=mapping))
        if name == "generate_report":
            return self._wrap_response(self.sdk.report_path(path, mapping=mapping))
        return {
            "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
            "isError": True,
        }

    def handle(self, request_obj: dict[str, Any]) -> dict[str, Any]:
        req_id = request_obj.get("id")
        method = request_obj.get("method")
        if method in {"initialize", "mcp/initialize"}:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "serverInfo": {"name": "external-data-workbench", "version": "1.0"},
                    "capabilities": {"tools": {}},
                },
            }
        if method in {"tools/list", "mcp/tools/list"}:
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": self.tool_specs()}}
        if method in {"tools/call", "mcp/tools/call"}:
            params = request_obj.get("params") or {}
            return {"jsonrpc": "2.0", "id": req_id, "result": self.call_tool(params.get("name", ""), params.get("arguments") or {})}
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Unknown method: {method}"},
        }


def serve_stdio() -> None:
    server = WorkbenchMCPServer()
    for line in sys.stdin:
        text = line.strip()
        if not text:
            continue
        try:
            request_obj = json.loads(text)
            response_obj = server.handle(request_obj)
        except Exception as exc:  # noqa: BLE001
            response_obj = {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": str(exc)}}
        sys.stdout.write(json.dumps(response_obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    serve_stdio()
