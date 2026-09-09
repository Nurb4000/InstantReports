"""Side-by-side version comparison service.

Transforms the semantic diff into a format suitable for UI rendering,
showing old vs new values with changes highlighted.
"""
from __future__ import annotations

from typing import Any

from app.services.versioning.diff import ReportDiffEngine


def compare_versions(
    old_def: dict[str, Any],
    new_def: dict[str, Any],
) -> dict[str, Any]:
    """Compare two report definitions and return a structured comparison.

    Returns a dict with:
    - summary: High-level stats (sections added/removed/modified, etc.)
    - sections: List of section comparisons with changes highlighted
    - data_sources: List of data source comparisons
    - parameters: List of parameter changes
    """
    engine = ReportDiffEngine()
    diff = engine.diff(old_def, new_def)

    comparison = {
        "summary": _build_summary(diff),
        "sections": _compare_sections(old_def, new_def, diff),
        "data_sources": _compare_data_sources(old_def, new_def, diff),
        "parameters": _compare_parameters(old_def, new_def),
    }

    return comparison


def _build_summary(diff: dict[str, Any]) -> dict[str, Any]:
    """Build a high-level summary of changes."""
    has_changes = bool(
        diff.get("sections_added")
        or diff.get("sections_removed")
        or diff.get("sections_modified")
        or diff.get("data_sources_added")
        or diff.get("data_sources_removed")
        or diff.get("data_sources_modified")
        or diff.get("parameters_changed")
    )
    return {
        "sections_added": len(diff.get("sections_added", [])),
        "sections_removed": len(diff.get("sections_removed", [])),
        "sections_modified": len(diff.get("sections_modified", [])),
        "data_sources_added": len(diff.get("data_sources_added", [])),
        "data_sources_removed": len(diff.get("data_sources_removed", [])),
        "data_sources_modified": len(diff.get("data_sources_modified", [])),
        "has_changes": has_changes,
    }


def _compare_sections(
    old_def: dict[str, Any],
    new_def: dict[str, Any],
    diff: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compare sections between old and new definitions."""
    old_sections = old_def.get("layout", {}).get("sections", [])
    new_sections = new_def.get("layout", {}).get("sections", [])

    comparisons = []

    # Compare existing sections by index
    max_len = max(len(old_sections), len(new_sections))
    for i in range(max_len):
        old_section = old_sections[i] if i < len(old_sections) else None
        new_section = new_sections[i] if i < len(new_sections) else None

        comparison = {
            "index": i,
            "type": new_section.get("type", "detail") if new_section else "removed",
        }

        if old_section and new_section:
            # Both exist - check for modifications
            engine = ReportDiffEngine()
            section_changes = engine._diff_section(old_section, new_section)
            if section_changes:
                comparison["status"] = "modified"
                comparison["changes"] = section_changes
            else:
                comparison["status"] = "unchanged"
            comparison["old"] = old_section
            comparison["new"] = new_section
        elif new_section:
            # Added
            comparison["status"] = "added"
            comparison["new"] = new_section
        else:
            # Removed
            comparison["status"] = "removed"
            comparison["old"] = old_section

        comparisons.append(comparison)

    return comparisons


def _compare_data_sources(
    old_def: dict[str, Any],
    new_def: dict[str, Any],
    diff: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compare data sources between old and new definitions."""
    old_ds = {ds["id"]: ds for ds in old_def.get("data_sources", [])}
    new_ds = {ds["id"]: ds for ds in new_def.get("data_sources", [])}

    comparisons = []
    all_ids = set(old_ds.keys()) | set(new_ds.keys())

    for ds_id in sorted(all_ids):
        comparison = {"id": ds_id}

        if ds_id in old_ds and ds_id in new_ds:
            # Both exist - check for modifications
            if old_ds[ds_id] != new_ds[ds_id]:
                comparison["status"] = "modified"
                comparison["changes"] = _deep_diff(old_ds[ds_id], new_ds[ds_id])
                comparison["old"] = old_ds[ds_id]
                comparison["new"] = new_ds[ds_id]
            else:
                comparison["status"] = "unchanged"
                comparison["old"] = old_ds[ds_id]
                comparison["new"] = new_ds[ds_id]
        elif ds_id in new_ds:
            comparison["status"] = "added"
            comparison["new"] = new_ds[ds_id]
        else:
            comparison["status"] = "removed"
            comparison["old"] = old_ds[ds_id]

        comparisons.append(comparison)

    return comparisons


def _compare_parameters(
    old_def: dict[str, Any],
    new_def: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compare parameters between old and new definitions."""
    old_params = {p["name"]: p for p in old_def.get("parameters", [])}
    new_params = {p["name"]: p for p in new_def.get("parameters", [])}

    comparisons = []
    all_names = set(old_params.keys()) | set(new_params.keys())

    for param_name in sorted(all_names):
        comparison = {"name": param_name}

        if param_name in old_params and param_name in new_params:
            if old_params[param_name] != new_params[param_name]:
                comparison["status"] = "modified"
                comparison["changes"] = _deep_diff(old_params[param_name], new_params[param_name])
                comparison["old"] = old_params[param_name]
                comparison["new"] = new_params[param_name]
            else:
                comparison["status"] = "unchanged"
        elif param_name in new_params:
            comparison["status"] = "added"
            comparison["new"] = new_params[param_name]
        else:
            comparison["status"] = "removed"
            comparison["old"] = old_params[param_name]

        comparisons.append(comparison)

    return comparisons


def _deep_diff(old: Any, new: Any, path: str = "") -> list[dict[str, Any]]:
    """Recursively diff two values and return a list of changes."""
    changes = []

    if isinstance(old, dict) and isinstance(new, dict):
        for key in set(old.keys()) | set(new.keys()):
            child_path = f"{path}.{key}" if path else key
            if key not in old:
                changes.append({
                    "path": child_path,
                    "type": "added",
                    "value": new[key],
                })
            elif key not in new:
                changes.append({
                    "path": child_path,
                    "type": "removed",
                    "value": old[key],
                })
            else:
                child_changes = _deep_diff(old[key], new[key], child_path)
                changes.extend(child_changes)
    elif isinstance(old, list) and isinstance(new, list):
        if old != new:
            changes.append({
                "path": path,
                "type": "modified",
                "old": old,
                "new": new,
            })
    else:
        if old != new:
            changes.append({
                "path": path,
                "type": "modified",
                "old": old,
                "new": new,
            })

    return changes
