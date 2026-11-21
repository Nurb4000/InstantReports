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


class DefinitionValidationError(ValueError):
    """Raised when a report definition fails structural/schema validation.

    Used at the import/create boundaries (see :func:`validate_report_definition`)
    to reject malformed or hostile definitions before they are persisted and later
    rendered/exported, where they could stress the engine.
    """


# Hard bounds that keep a definition renderable without exhausting memory/CPU.
# Chosen generously above realistic report sizes so legitimate reports are never
# rejected; anything beyond these is treated as a DoS vector and rejected.
_MAX_SECTIONS = 200
_MAX_ELEMENTS_PER_SECTION = 1000
_MAX_TOTAL_ELEMENTS = 5000
_MAX_DATA_SOURCES = 100
_MAX_PARAMETERS = 200
_MAX_STRING_LEN = 20_000
_MAX_WALK_DEPTH = 64


def _check_string(value: Any, path: str) -> None:
    """Reject strings longer than the cap anywhere in the definition."""
    if isinstance(value, str):
        if len(value) > _MAX_STRING_LEN:
            raise DefinitionValidationError(
                f"String at '{path}' exceeds {_MAX_STRING_LEN} characters"
            )


def _walk_strings(value: Any, path: str, depth: int) -> None:
    """Recursively enforce the string-length cap across the whole definition.

    A hostile definition could embed a huge query/name/property anywhere, so the
    cap is applied to every string, not just the top-level fields. Bounded by
    depth to avoid pathological recursion.
    """
    if depth > _MAX_WALK_DEPTH:
        raise DefinitionValidationError("Definition nesting is too deep")
    if isinstance(value, dict):
        for k, v in value.items():
            _walk_strings(v, f"{path}.{k}", depth + 1)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _walk_strings(v, f"{path}[{i}]", depth + 1)
    else:
        _check_string(value, path)


def validate_report_definition(definition: Any) -> None:
    """Validate a report definition's structure and size.

    Raises :class:`DefinitionValidationError` if ``definition`` is not a dict,
    has structurally wrong top-level keys, exceeds element/count/string bounds,
    or contains malformed nested sections/elements. This complements
    :func:`normalize_report_definition`, which only fills defaults; validation
    rejects inputs that would otherwise be persisted and later rendered against
    live data (the export/import gap). Unknown extra top-level keys are allowed.
    """
    if not isinstance(definition, dict):
        raise DefinitionValidationError("Definition must be a JSON object")

    layout = definition.get("layout")
    if layout is not None and not isinstance(layout, dict):
        raise DefinitionValidationError("'layout' must be an object")

    sections = layout.get("sections") if isinstance(layout, dict) else None
    total_elements = 0
    if sections is not None:
        if not isinstance(sections, list):
            raise DefinitionValidationError("'layout.sections' must be a list")
        if len(sections) > _MAX_SECTIONS:
            raise DefinitionValidationError(
                f"Too many sections: {len(sections)} (max {_MAX_SECTIONS})"
            )
        for si, section in enumerate(sections):
            if not isinstance(section, dict):
                raise DefinitionValidationError(f"section[{si}] must be an object")
            elements = section.get("elements")
            if elements is not None:
                if not isinstance(elements, list):
                    raise DefinitionValidationError(
                        f"section[{si}].elements must be a list"
                    )
                if len(elements) > _MAX_ELEMENTS_PER_SECTION:
                    raise DefinitionValidationError(
                        f"section[{si}].elements has {len(elements)} "
                        f"(max {_MAX_ELEMENTS_PER_SECTION})"
                    )
                for ei, element in enumerate(elements):
                    total_elements += 1
                    if not isinstance(element, dict):
                        raise DefinitionValidationError(
                            f"element[{si},{ei}] must be an object"
                        )
                    element_type = element.get("type")
                    if element_type is not None and not isinstance(element_type, str):
                        raise DefinitionValidationError(
                            f"element[{si},{ei}].type must be a string"
                        )
                    props = element.get("properties")
                    if props is not None and not isinstance(props, dict):
                        raise DefinitionValidationError(
                            f"element[{si},{ei}].properties must be an object"
                        )
    if total_elements > _MAX_TOTAL_ELEMENTS:
        raise DefinitionValidationError(
            f"Too many total elements: {total_elements} (max {_MAX_TOTAL_ELEMENTS})"
        )

    data_sources = definition.get("data_sources")
    if data_sources is not None:
        if not isinstance(data_sources, list):
            raise DefinitionValidationError("'data_sources' must be a list")
        if len(data_sources) > _MAX_DATA_SOURCES:
            raise DefinitionValidationError(
                f"Too many data sources: {len(data_sources)} (max {_MAX_DATA_SOURCES})"
            )

    parameters = definition.get("parameters")
    if parameters is not None:
        if not isinstance(parameters, list):
            raise DefinitionValidationError("'parameters' must be a list")
        if len(parameters) > _MAX_PARAMETERS:
            raise DefinitionValidationError(
                f"Too many parameters: {len(parameters)} (max {_MAX_PARAMETERS})"
            )

    # Enforce the per-string length cap across the entire definition so a huge
    # embedded query/name/property cannot be persisted and later blow up export.
    _walk_strings(definition, "$", 0)
