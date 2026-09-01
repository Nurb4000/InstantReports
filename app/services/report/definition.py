"""Pure helpers for normalizing report definitions.

Kept free of any FastAPI/route imports so the logic can be unit-tested in
isolation (importing a route module pulls in ``app.main`` -> fastapi).
"""

from __future__ import annotations

import json
from typing import Any

# The canonical shape every report definition should expose. Kept as a plain
# dict so it can be deep-copied safely for each call.
DEFAULT_REPORT_DEFINITION: dict[str, Any] = {
    "layout": {"sections": []},
    "data_sources": [],
    "parameters": [],
}


def normalize_report_definition(definition: Any) -> dict[str, Any]:
    """Return a complete report definition with all standard top-level keys.

    Accepts ``None``, an empty value, or a partial definition and merges it on
    top of :data:`DEFAULT_REPORT_DEFINITION` so downstream code can rely on
    every key existing. A deep copy is returned to avoid mutating the default.
    """
    result = json.loads(json.dumps(DEFAULT_REPORT_DEFINITION))
    if isinstance(definition, dict):
        for key, value in definition.items():
            result[key] = value
    return result
