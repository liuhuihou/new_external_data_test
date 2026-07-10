from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tools.data_tools import AnalyzeDatasetTool, UploadDatasetTool
from tools.report_tools import GenerateReportTool


@dataclass
class AgentResponse:
    ok: bool
    data: dict[str, Any] | None = None
    error: str = ""


class DataTestingAgent:
    """Coordinates dataset upload, analysis, and report generation."""

    def __init__(self, backend: Any, datasets: dict[str, dict[str, Any]], analyses: dict[str, dict[str, Any]]):
        self.backend = backend
        self.datasets = datasets
        self.analyses = analyses
        self.upload_tool = UploadDatasetTool(backend)
        self.analyze_tool = AnalyzeDatasetTool(backend)
        self.report_tool = GenerateReportTool(backend)

    def upload(self, *, file_name: str, raw: bytes) -> AgentResponse:
        result = self.upload_tool.run(file_name=file_name, raw=raw, datasets=self.datasets)
        return AgentResponse(ok=result.ok, data=result.data, error=result.error)

    def analyze(self, *, dataset_id: str, mapping: dict[str, str] | None = None) -> AgentResponse:
        dataset = self.datasets.get(dataset_id)
        if not dataset:
            return AgentResponse(ok=False, error="数据集不存在或服务已重启，请重新上传")
        selected_mapping = mapping or self.backend.infer_mapping(dataset)
        result = self.analyze_tool.run(dataset=dataset, mapping=selected_mapping)
        if result.ok and result.data:
            self.analyses[dataset_id] = result.data["analysis"]
        return AgentResponse(ok=result.ok, data=result.data, error=result.error)

    def report(self, *, dataset_id: str, mapping: dict[str, str] | None = None) -> AgentResponse:
        dataset = self.datasets.get(dataset_id)
        if not dataset:
            return AgentResponse(ok=False, error="数据集不存在或服务已重启，请重新上传")
        analysis = self.analyses.get(dataset_id)
        if analysis is None:
            analyzed = self.analyze(dataset_id=dataset_id, mapping=mapping)
            if not analyzed.ok:
                return analyzed
            analysis = analyzed.data["analysis"] if analyzed.data else None
        result = self.report_tool.run(analysis=analysis)
        if result.ok and result.data:
            data = {"report": result.data["report"], "analysis": analysis}
        else:
            data = None
        return AgentResponse(ok=result.ok, data=data, error=result.error)
