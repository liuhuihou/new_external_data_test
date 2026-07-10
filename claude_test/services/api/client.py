from __future__ import annotations

from typing import Any


class ApiClient:
    """Thin API client facade.

    The current implementation delegates to the backend module functions so the
    existing behavior remains stable while the architecture gains a reusable API
    boundary.
    """

    def __init__(self, backend: Any):
        self.backend = backend

    def public_config(self) -> dict[str, Any]:
        return self.backend.public_api_config()

    def generate_report_text(self, analysis: dict[str, Any]) -> tuple[str | None, str | None]:
        return self.backend.call_ai_report_api(analysis)
