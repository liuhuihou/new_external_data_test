from __future__ import annotations

from typing import Any


def load_config(backend: Any) -> dict[str, Any]:
    return backend.load_api_config()


def public_config(backend: Any) -> dict[str, Any]:
    return backend.public_api_config()
