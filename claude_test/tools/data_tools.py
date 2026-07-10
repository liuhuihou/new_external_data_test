from __future__ import annotations

import uuid
from typing import Any

from tools.base import ToolResult


class UploadDatasetTool:
    name = "upload_dataset"

    def __init__(self, backend: Any):
        self.backend = backend

    def run(self, *, file_name: str, raw: bytes, datasets: dict[str, dict[str, Any]]) -> ToolResult:
        try:
            dataset = self.backend.parse_file(file_name, raw)
            dataset_id = uuid.uuid4().hex
            datasets[dataset_id] = dataset
            mapping = self.backend.infer_mapping(dataset)
            return ToolResult(ok=True, data={
                "datasetId": dataset_id,
                "fileName": dataset["file_name"],
                "sheetName": dataset["sheet_name"],
                "rows": len(dataset["rows"]),
                "cols": len(dataset["headers"]),
                "headers": dataset["headers"],
                "descriptions": dataset["descriptions"],
                "preview": dataset["rows"][:12],
                "mapping": mapping,
                "numericColumns": self.backend.numeric_columns(dataset),
                "badValueOptions": self.backend.unique_values(dataset, mapping["yCol"], 200) if mapping.get("yCol") else [],
            })
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, error=str(exc))


class AnalyzeDatasetTool:
    name = "analyze_dataset"

    def __init__(self, backend: Any):
        self.backend = backend

    def run(self, *, dataset: dict[str, Any], mapping: dict[str, str]) -> ToolResult:
        try:
            analysis = self.backend.analyze_dataset(dataset, mapping)
            return ToolResult(ok=True, data={"analysis": analysis, "qualityMetrics": self.backend.QUALITY_METRICS})
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, error=str(exc))
