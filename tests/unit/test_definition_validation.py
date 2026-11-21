"""Unit tests for report definition schema validation (Gap #4).

Covers :func:`validate_report_definition`, which rejects malformed or hostile
definitions before they are persisted and later rendered/exported against live
data -- the export/import gap.
"""
from __future__ import annotations

import pytest

from app.services.report.definition import (
    DefinitionValidationError,
    validate_report_definition,
)


def _valid_definition():
    return {
        "layout": {
            "sections": [
                {"type": "header"},
                {
                    "type": "detail",
                    "elements": [
                        {"type": "table", "properties": {"query": "SELECT 1"}},
                        {"type": "chart", "properties": {"query": "SELECT 2"}},
                    ],
                },
            ]
        },
        "data_sources": [{"id": "ds1"}],
        "parameters": [{"name": "p1"}],
    }


@pytest.mark.parametrize("definition", [None, [], "str", 42, True])
def test_non_object_rejected(definition):
    with pytest.raises(DefinitionValidationError):
        validate_report_definition(definition)


def test_valid_definition_passes():
    # Should not raise.
    validate_report_definition(_valid_definition())


def test_missing_top_level_keys_ok():
    # Partial definitions (only layout) are structurally valid.
    validate_report_definition({"layout": {"sections": [{"type": "header"}]}})


def test_layout_not_object_rejected():
    with pytest.raises(DefinitionValidationError):
        validate_report_definition({"layout": []})


def test_sections_not_list_rejected():
    with pytest.raises(DefinitionValidationError):
        validate_report_definition({"layout": {"sections": {}}})


def test_section_not_object_rejected():
    with pytest.raises(DefinitionValidationError):
        validate_report_definition({"layout": {"sections": ["nope"]}})


def test_elements_not_list_rejected():
    with pytest.raises(DefinitionValidationError):
        validate_report_definition(
            {"layout": {"sections": [{"elements": {}}]}}
        )


def test_element_not_object_rejected():
    with pytest.raises(DefinitionValidationError):
        validate_report_definition(
            {"layout": {"sections": [{"elements": [123]}]}}
        )


def test_element_type_must_be_string():
    with pytest.raises(DefinitionValidationError):
        validate_report_definition(
            {"layout": {"sections": [{"elements": [{"type": 5}]}]}}
        )


def test_properties_must_be_object():
    with pytest.raises(DefinitionValidationError):
        validate_report_definition(
            {"layout": {"sections": [{"elements": [{"properties": "x"}]}]}}
        )


def test_data_sources_must_be_list():
    with pytest.raises(DefinitionValidationError):
        validate_report_definition({"data_sources": {}})


def test_parameters_must_be_list():
    with pytest.raises(DefinitionValidationError):
        validate_report_definition({"parameters": {}})


def test_too_many_sections_rejected():
    sections = [{"type": "detail", "elements": []} for _ in range(201)]
    with pytest.raises(DefinitionValidationError):
        validate_report_definition({"layout": {"sections": sections}})


def test_too_many_elements_per_section_rejected():
    elements = [{"type": "table"} for _ in range(1001)]
    with pytest.raises(DefinitionValidationError):
        validate_report_definition(
            {"layout": {"sections": [{"elements": elements}]}}
        )


def test_too_many_total_elements_rejected():
    # Two sections of 3000 elements each = 6000 > 5000 cap.
    elems = [{"type": "table"} for _ in range(3000)]
    with pytest.raises(DefinitionValidationError):
        validate_report_definition(
            {"layout": {"sections": [{"elements": elems}, {"elements": elems}]}}
        )


def test_boundary_counts_allowed():
    # Exactly at the caps must be allowed.
    sections = [{"type": "detail", "elements": []} for _ in range(200)]
    validate_report_definition({"layout": {"sections": sections}})


def test_huge_string_rejected():
    big = "x" * (20_000 + 1)
    with pytest.raises(DefinitionValidationError):
        validate_report_definition(
            {"layout": {"sections": [{"elements": [{"properties": {"query": big}}]}]}}
        )


def test_max_length_string_allowed():
    exactly = "x" * 20_000
    validate_report_definition(
        {"layout": {"sections": [{"elements": [{"properties": {"query": exactly}}]}]}}
    )


def test_huge_name_string_rejected():
    big = "n" * (20_000 + 1)
    with pytest.raises(DefinitionValidationError):
        validate_report_definition({"layout": {"sections": [], "name": big}})


def test_too_many_data_sources_rejected():
    with pytest.raises(DefinitionValidationError):
        validate_report_definition({"data_sources": [{"id": i} for i in range(101)]})


def test_too_many_parameters_rejected():
    with pytest.raises(DefinitionValidationError):
        validate_report_definition({"parameters": [{"name": n} for n in range(201)]})


def test_unknown_extra_keys_allowed():
    # Extra top-level keys are permitted (forward-compat).
    validate_report_definition(
        {"layout": {"sections": []}, "custom_flag": True, "meta": {"a": 1}}
    )
