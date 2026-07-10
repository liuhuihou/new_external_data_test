from __future__ import annotations

from pathlib import Path
from typing import Any

import server as backend
from agents.data_testing_agent import DataTestingAgent


class WorkbenchSDK:
    """Reusable library entrypoint for upload, analysis, and reporting."""

    def __init__(self, backend_module: Any = backend):
        self.backend = backend_module
        self.datasets: dict[str, dict[str, Any]] = {}
        self.analyses: dict[str, dict[str, Any]] = {}
        self.agent = DataTestingAgent(self.backend, self.datasets, self.analyses)

    def load_api_config(self) -> dict[str, Any]:
        return self.backend.load_api_config()

    def public_api_config(self) -> dict[str, Any]:
        return self.backend.public_api_config()

    def upload_bytes(self, *, file_name: str, raw: bytes):
        return self.agent.upload(file_name=file_name, raw=raw)

    def upload_path(self, path: str | Path):
        file_path = Path(path).expanduser()
        return self.upload_bytes(file_name=file_path.name, raw=file_path.read_bytes())

    def analyze_path(self, path: str | Path, mapping: dict[str, str] | None = None):
        upload = self.upload_path(path)
        if not upload.ok or not upload.data:
            return upload
        return self.agent.analyze(dataset_id=upload.data["datasetId"], mapping=mapping)

    def report_path(self, path: str | Path, mapping: dict[str, str] | None = None):
        upload = self.upload_path(path)
        if not upload.ok or not upload.data:
            return upload
        return self.agent.report(dataset_id=upload.data["datasetId"], mapping=mapping)
