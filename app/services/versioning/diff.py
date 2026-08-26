from __future__ import annotations

import json
from typing import Any


class ReportDiffEngine:
    """Semantic diff engine for report definitions."""

    def diff(self, old_def: dict[str, Any], new_def: dict[str, Any]) -> dict[str, Any]:
        changes = {
            "sections_added": [],
            "sections_removed": [],
            "sections_modified": [],
            "data_sources_added": [],
            "data_sources_removed": [],
            "data_sources_modified": [],
            "parameters_changed": [],
        }

        old_sections = self._get_sections(old_def)
        new_sections = self._get_sections(new_def)

        old_section_types = {s.get("type"): i for i, s in enumerate(old_sections)}
        new_section_types = {s.get("type"): i for i, s in enumerate(new_sections)}

        for section_type in set(old_section_types.keys()) | set(new_section_types.keys()):
            if section_type not in old_section_types and section_type in new_section_types:
                changes["sections_added"].append(section_type)
            elif section_type in old_section_types and section_type not in new_section_types:
                changes["sections_removed"].append(section_type)
            else:
                old_section = old_sections[old_section_types[section_type]]
                new_section = new_sections[new_section_types[section_type]]
                section_changes = self._diff_section(old_section, new_section)
                if section_changes:
                    changes["sections_modified"].append({
                        "type": section_type,
                        "changes": section_changes,
                    })

        old_ds = {ds["id"]: ds for ds in old_def.get("data_sources", [])}
        new_ds = {ds["id"]: ds for ds in new_def.get("data_sources", [])}

        for ds_id in set(old_ds.keys()) | set(new_ds.keys()):
            if ds_id not in old_ds and ds_id in new_ds:
                changes["data_sources_added"].append(ds_id)
            elif ds_id in old_ds and ds_id not in new_ds:
                changes["data_sources_removed"].append(ds_id)
            else:
                if old_ds[ds_id] != new_ds[ds_id]:
                    changes["data_sources_modified"].append(ds_id)

        return changes

    def _get_sections(self, definition: dict[str, Any]) -> list[dict[str, Any]]:
        layout = definition.get("layout", {})
        return layout.get("sections", [])

    def _diff_section(
        self, old_section: dict[str, Any], new_section: dict[str, Any]
    ) -> list[dict[str, Any]]:
        changes = []

        if old_section.get("type") != new_section.get("type"):
            changes.append({
                "type": "property_changed",
                "property": "type",
                "old": old_section.get("type"),
                "new": new_section.get("type"),
            })

        if old_section.get("data_source") != new_section.get("data_source"):
            changes.append({
                "type": "property_changed",
                "property": "data_source",
                "old": old_section.get("data_source"),
                "new": new_section.get("data_source"),
            })

        if old_section.get("group_by") != new_section.get("group_by"):
            changes.append({
                "type": "property_changed",
                "property": "group_by",
                "old": old_section.get("group_by"),
                "new": new_section.get("group_by"),
            })

        old_elements = old_section.get("elements", [])
        new_elements = new_section.get("elements", [])

        if len(old_elements) != len(new_elements):
            changes.append({
                "type": "element_count_changed",
                "old_count": len(old_elements),
                "new_count": len(new_elements),
            })
        else:
            for i, (old_el, new_el) in enumerate(zip(old_elements, new_elements)):
                if old_el != new_el:
                    element_changes = self._diff_element(old_el, new_el)
                    if element_changes:
                        changes.append({
                            "type": "element_modified",
                            "index": i,
                            "changes": element_changes,
                        })

        return changes

    def _diff_element(
        self, old_element: dict[str, Any], new_element: dict[str, Any]
    ) -> list[dict[str, Any]]:
        changes = []

        if old_element.get("type") != new_element.get("type"):
            changes.append({
                "type": "property_changed",
                "property": "type",
                "old": old_element.get("type"),
                "new": new_element.get("type"),
            })

        for key in set(old_element.keys()) | set(new_element.keys()):
            if key in ("id", "created_at", "updated_at"):
                continue
            old_val = old_element.get(key)
            new_val = new_element.get(key)
            if old_val != new_val:
                changes.append({
                    "type": "property_changed",
                    "property": key,
                    "old": self._serialize(old_val),
                    "new": self._serialize(new_val),
                })

        return changes

    @staticmethod
    def _serialize(value: Any) -> str:
        if isinstance(value, (dict, list)):
            return json.dumps(value, sort_keys=True)
        return str(value)
