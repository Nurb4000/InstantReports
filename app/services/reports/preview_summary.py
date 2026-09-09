"""Generate a small HTML preview summary of a report layout.

Used by the schedule list to show users a quick overview of what each
scheduled report will look like without running the full export.
"""
from __future__ import annotations

from typing import Any


def generate_report_preview_summary(definition: dict[str, Any] | None) -> str:
    """Generate an HTML summary of a report's layout structure.

    Returns a small HTML snippet showing section types and element counts.
    Useful for schedule list thumbnails so users can confirm layout before running.

    Args:
        definition: The report definition dict (from Report.definition).

    Returns:
        HTML string with layout summary, or empty string if no definition.
    """
    if definition is None:
        return ""

    sections = definition.get("layout", {}).get("sections", [])
    if not sections:
        return "<em>No sections defined</em>"

    summary_parts = []
    for section in sections:
        section_type = section.get("type", "detail")
        elements = section.get("elements", [])
        element_types = {e.get("type", "unknown") for e in elements}
        summary_parts.append(
            f"<div class='preview-section'><strong>{section_type.capitalize()}:</strong> "
            f"{len(elements)} element(s) [{', '.join(sorted(element_types))}]</div>"
        )

    return "<div class='preview-summary'>" + "".join(summary_parts) + "</div>"
