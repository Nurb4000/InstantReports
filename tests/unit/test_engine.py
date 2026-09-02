"""Unit tests for report engine."""
import pandas as pd

from app.services.engine.calculated_fields import CalculatedFieldEvaluator
from app.services.engine.conditional_formatting import ConditionalFormatter
from app.services.engine.data_processor import DataProcessor
from app.services.engine.renderer import ReportRenderer


class TestReportRenderer:
    """Test report rendering."""

    def test_render_empty_report(self):
        """Should render an empty report definition."""
        renderer = ReportRenderer()
        definition = {
            "name": "Test Report",
            "layout": {"sections": []}
        }
        result = renderer.render(definition, {})
        
        assert result["name"] == "Test Report"
        assert result["sections"] == []

    def test_render_text_element(self):
        """Should render text elements."""
        renderer = ReportRenderer()
        definition = {
            "name": "Test",
            "layout": {
                "sections": [
                    {
                        "type": "header",
                        "elements": [
                            {"type": "text", "content": "Hello World"}
                        ]
                    }
                ]
            }
        }
        result = renderer.render(definition, {})
        
        assert len(result["sections"]) == 1
        assert result["sections"][0]["elements"][0]["type"] == "text"
        assert result["sections"][0]["elements"][0]["content"] == "Hello World"

    def test_render_table_element(self):
        """Should render table elements with data."""
        renderer = ReportRenderer()
        df = pd.DataFrame({
            "name": ["Alice", "Bob"],
            "score": [95, 87]
        })
        
        definition = {
            "name": "Test",
            "layout": {
                "sections": [
                    {
                        "type": "detail",
                        "data_source": "ds1",
                        "elements": [
                            {
                                "type": "table",
                                "data_source": "ds1",
                                "columns": [
                                    {"field": "name", "header": "Name"},
                                    {"field": "score", "header": "Score"}
                                ]
                            }
                        ]
                    }
                ]
            },
            "data_sources": {"ds1": df}
        }
        
        result = renderer.render(definition, {"ds1": df})
        
        assert len(result["sections"]) == 1
        table_element = result["sections"][0]["elements"][0]
        assert table_element["type"] == "table"
        assert len(table_element["data"]) == 2
        assert table_element["total_rows"] == 2

    def test_render_table_applies_conditional_formatting(self):
        """Should attach formatting to rows that match rules."""
        renderer = ReportRenderer()
        df = pd.DataFrame({"name": ["Alice", "Bob"], "score": [95, 60]})

        definition = {
            "name": "Test",
            "layout": {
                "sections": [
                    {
                        "type": "detail",
                        "elements": [
                            {
                                "type": "table",
                                "data_source": "ds1",
                                "columns": [
                                    {"field": "name", "header": "Name"},
                                    {"field": "score", "header": "Score"},
                                ],
                                "formatting_rules": [
                                    {
                                        "target": "row",
                                        "condition": {"field": "score", "operator": "<", "value": 70},
                                        "format": {"background": "#ffcccc"},
                                    }
                                ],
                            }
                        ],
                    }
                ]
            },
        }

        result = renderer.render(definition, {"ds1": df})
        table = result["sections"][0]["elements"][0]

        assert table["data"][0]["formatting"]["row"] is None
        assert table["data"][1]["formatting"]["row"] == {"background": "#ffcccc"}

    def test_render_table_without_formatting_rules_has_no_formatting(self):
        """Tables without rules render plain data."""
        renderer = ReportRenderer()
        df = pd.DataFrame({"name": ["Alice"], "score": [95]})

        definition = {
            "name": "Test",
            "layout": {
                "sections": [
                    {
                        "type": "detail",
                        "elements": [
                            {
                                "type": "table",
                                "data_source": "ds1",
                                "columns": [
                                    {"field": "name", "header": "Name"},
                                    {"field": "score", "header": "Score"},
                                ],
                            }
                        ],
                    }
                ]
            },
        }

        result = renderer.render(definition, {"ds1": df})
        table = result["sections"][0]["elements"][0]

        assert "formatting" not in table["data"][0]

    def test_render_subreport_reads_nested_properties(self):
        """Sub-report config stored under element.properties is honored."""
        renderer = ReportRenderer()
        definition = {
            "name": "Test",
            "layout": {
                "sections": [
                    {
                        "type": "detail",
                        "elements": [
                            {
                                "type": "subreport",
                                "properties": {
                                    "reportId": "child-123",
                                    "render_mode": "page",
                                    "pass_parameters": {"region": "region"},
                                },
                            }
                        ],
                    }
                ]
            },
        }

        result = renderer.render(definition, {})
        subreport = result["sections"][0]["elements"][0]

        assert subreport["type"] == "subreport"
        assert subreport["render_mode"] == "page"
        assert subreport["pass_parameters"] == {"region": "region"}

    def test_render_subreport_defaults_when_no_properties(self):
        """Sub-report falls back to inline mode and empty params."""
        renderer = ReportRenderer()
        definition = {
            "name": "Test",
            "layout": {"sections": [{"type": "detail", "elements": [{"type": "subreport"}]}]},
        }

        result = renderer.render(definition, {})
        subreport = result["sections"][0]["elements"][0]

        assert subreport["render_mode"] == "inline"
        assert subreport["pass_parameters"] == {}

    def test_resolve_tokens(self):
        """Should resolve special tokens."""
        renderer = ReportRenderer()
        context = {
            "page_number": 5,
            "total_pages": 10,
            "report_name": "Test Report",
            "user_name": "John Doe"
        }
        
        text = "Page {{page.number}} of {{page.total}} - {{report.name}} by {{user.name}}"
        result = renderer.resolve_tokens(text, context)
        
        assert "Page 5 of 10" in result
        assert "Test Report" in result
        assert "John Doe" in result


class TestDataProcessor:
    """Test data processing."""

    def test_filter_data_equals(self):
        """Should filter data with equals operator."""
        processor = DataProcessor()
        df = pd.DataFrame({"name": ["Alice", "Bob", "Charlie"], "score": [90, 85, 95]})
        
        filters = [{"field": "name", "operator": "==", "value": "Bob"}]
        result = processor.filter_data(df, filters)
        
        assert len(result) == 1
        assert result.iloc[0]["name"] == "Bob"

    def test_filter_data_greater_than(self):
        """Should filter data with greater than operator."""
        processor = DataProcessor()
        df = pd.DataFrame({"name": ["Alice", "Bob"], "score": [90, 85]})
        
        filters = [{"field": "score", "operator": ">", "value": 87}]
        result = processor.filter_data(df, filters)
        
        assert len(result) == 1
        assert result.iloc[0]["name"] == "Alice"

    def test_filter_data_contains(self):
        """Should filter data with contains operator."""
        processor = DataProcessor()
        df = pd.DataFrame({"name": ["Alice Smith", "Bob Jones", "Charlie Brown"]})
        
        filters = [{"field": "name", "operator": "contains", "value": "Smith"}]
        result = processor.filter_data(df, filters)
        
        assert len(result) == 1
        assert result.iloc[0]["name"] == "Alice Smith"

    def test_process_adds_calculated_fields(self):
        """Should append calculated fields defined at the report level."""
        processor = DataProcessor()
        df = pd.DataFrame({"revenue": [100, 200], "cost": [40, 50]})

        definition = {
            "calculated_fields": [
                {"name": "profit", "expression": "{{revenue}} - {{cost}}"},
            ],
        }

        result = processor.process(df, definition)

        assert "profit" in result.columns
        assert list(result["profit"]) == [60, 150]

    def test_process_calculated_field_with_multiple_refs(self):
        """Should support expressions referencing several source fields."""
        processor = DataProcessor()
        df = pd.DataFrame({"amount": [10, 20], "rate": [1.5, 2.5]})

        definition = {
            "calculated_fields": [
                {"name": "total", "expression": "{{amount}} * {{rate}}"},
            ],
        }

        result = processor.process(df, definition)

        assert list(result["total"]) == [15.0, 50.0]

    def test_process_missing_calculated_field_is_null(self):
        """Should leave a calculated field null when its expression fails."""
        processor = DataProcessor()
        df = pd.DataFrame({"a": [1, 2]})

        definition = {
            "calculated_fields": [
                {"name": "bad", "expression": "{{missing}} + 1"},
            ],
        }

        result = processor.process(df, definition)

        assert result["bad"].isna().all()


class TestConditionalFormatting:
    """Test conditional formatting."""

    def test_apply_rules_row_highlight(self):
        """Should apply row-level formatting rules."""
        formatter = ConditionalFormatter()
        data = [
            {"name": "Alice", "score": 95},
            {"name": "Bob", "score": 72},
            {"name": "Charlie", "score": 88}
        ]
        
        rules = [
            {
                "target": "row",
                "condition": {"field": "score", "operator": "<", "value": 75},
                "format": {"background": "#ffcccc"}
            }
        ]
        
        result = formatter.apply_rules(data, rules)
        
        assert result[0]["formatting"]["row"] is None
        assert result[1]["formatting"]["row"] == {"background": "#ffcccc"}
        assert result[2]["formatting"]["row"] is None

    def test_apply_rules_cell_highlight(self):
        """Should apply cell-level formatting rules."""
        formatter = ConditionalFormatter()
        data = [
            {"name": "Alice", "score": 95},
            {"name": "Bob", "score": 72}
        ]
        
        rules = [
            {
                "target": "cell",
                "field": "score",
                "condition": {"operator": ">=", "value": 90},
                "format": {"color": "#00ff00"}
            }
        ]
        
        result = formatter.apply_rules(data, rules)
        
        assert result[0]["formatting"]["cells"]["score"] == {"color": "#00ff00"}
        assert result[1]["formatting"]["cells"]["score"] is None

    def test_get_css_styles(self):
        """Should convert formatting to CSS."""
        formatter = ConditionalFormatter()
        formatting = {
            "row": {"background": "#ffcccc", "bold": True},
            "cells": {}
        }
        
        css = formatter.get_css_styles(formatting)
        
        assert "background-color: #ffcccc" in css
        assert "font-weight: bold" in css


class TestConditionalFormatterOperators:
    """Exercise every comparison operator in ConditionalFormatter._check_condition."""

    def _matches(self, operator, cell_value, condition_value):
        formatter = ConditionalFormatter()
        rule = {
            "target": "row",
            "condition": {"field": "v", "operator": operator, "value": condition_value},
            "format": {"background": "#fff"},
        }
        result = formatter.apply_rules([{"v": cell_value}], [rule])
        return result[0]["formatting"]["row"] is not None

    def test_equality_and_inequality(self):
        assert self._matches("==", 5, 5) is True
        assert self._matches("!=", 5, 6) is True
        assert self._matches("==", 5, 6) is False

    def test_numeric_ordering_coerces_strings(self):
        # Cell values arrive as strings from DB rows; operators must coerce.
        assert self._matches(">", "90", 80) is True
        assert self._matches("<=", "80", "80") is True
        assert self._matches(">", "10", 80) is False

    def test_between_and_not_between(self):
        assert self._matches("between", 50, [10, 100]) is True
        assert self._matches("between", 200, [10, 100]) is False
        assert self._matches("not_between", 200, [10, 100]) is True

    def test_contains_and_not_contains(self):
        assert self._matches("contains", "John", "ohn") is True
        assert self._matches("contains", "John", "xyz") is False
        assert self._matches("not_contains", "John", "xyz") is True

    def test_starts_with_and_ends_with(self):
        assert self._matches("starts_with", "Hello", "He") is True
        assert self._matches("ends_with", "Hello", "lo") is True
        assert self._matches("starts_with", "Hello", "lo") is False

    def test_is_empty_and_is_not_empty(self):
        assert self._matches("is_empty", "", None) is True
        assert self._matches("is_empty", None, None) is True
        assert self._matches("is_not_empty", "x", None) is True
        assert self._matches("is_not_empty", "", None) is False

    def test_mismatched_types_do_not_raise(self):
        # Comparing a string cell to a non-numeric condition must not crash.
        assert self._matches(">", "abc", 5) is False
    """Test calculated field evaluation."""

    def test_validate_expression_valid(self):
        """Should validate valid expressions."""
        evaluator = CalculatedFieldEvaluator()
        is_valid, _ = evaluator.validate_expression("{{revenue}} - {{cost}}")
        assert is_valid is True

    def test_validate_expression_empty(self):
        """Should reject empty expressions."""
        evaluator = CalculatedFieldEvaluator()
        is_valid, _ = evaluator.validate_expression("")
        assert is_valid is False

    def test_validate_expression_unbalanced_braces(self):
        """Should reject unbalanced braces."""
        evaluator = CalculatedFieldEvaluator()
        is_valid, _ = evaluator.validate_expression("{{revenue - cost")
        assert is_valid is False

    def test_evaluate_simple_expression(self):
        """Should evaluate simple column references."""
        evaluator = CalculatedFieldEvaluator()
        df = pd.DataFrame({"revenue": [100, 200], "cost": [50, 100]})
        
        result = evaluator.evaluate("{{revenue}} - {{cost}}", df)
        
        assert list(result) == [50, 100]


class TestElementRenderers:
    """Cover chart/crosstab/image rendering via _render_element (the real dispatch).

    These element types were previously untested; a regression here would silently
    break report rendering for users who add charts, crosstabs, or images. Testing
    through _render_element also exercises the element_label threading that the
    private _render_* methods require as a parameter.
    """

    def test_render_chart_uses_defaults(self):
        renderer = ReportRenderer()
        result = renderer._render_element({"type": "chart"}, {})
        assert result["type"] == "chart"
        assert result["chart_type"] == "bar"
        assert result["width"] == "100%"
        assert result["height"] == "200px"

    def test_render_chart_reads_config(self):
        renderer = ReportRenderer()
        element_def = {
            "type": "chart",
            "chart_type": "line",
            "x_field": "month",
            "y_field": "sales",
            "width": "600px",
            "label": "Sales Trend",
        }
        result = renderer._render_element(element_def, {})
        assert result["chart_type"] == "line"
        assert result["x_field"] == "month"
        assert result["y_field"] == "sales"
        assert result["label"] == "Sales Trend"

    def test_render_crosstab_empty_data(self):
        renderer = ReportRenderer()
        element_def = {
            "type": "crosstab",
            "data_source": "ds1",
            "rowField": "region",
            "columnField": "product",
            "valueField": "sales",
        }
        result = renderer._render_element(element_def, {"ds1": pd.DataFrame()})
        assert result["type"] == "crosstab"
        assert result["data"] == []

    def test_render_crosstab_missing_required_fields(self):
        renderer = ReportRenderer()
        element_def = {"type": "crosstab", "data_source": "ds1", "rowField": "region"}
        df = pd.DataFrame({"region": ["N"], "sales": [1]})
        result = renderer._render_element(element_def, {"ds1": df})
        assert result.get("error") == "Missing required fields"

    def test_render_crosstab_pivots_correctly(self):
        renderer = ReportRenderer()
        element_def = {
            "type": "crosstab",
            "data_source": "ds1",
            "rowField": "region",
            "columnField": "product",
            "valueField": "sales",
            "aggregation": "sum",
        }
        df = pd.DataFrame(
            [
                {"region": "North", "product": "A", "sales": 10},
                {"region": "North", "product": "B", "sales": 20},
                {"region": "South", "product": "A", "sales": 30},
                {"region": "South", "product": "B", "sales": 40},
            ]
        )
        result = renderer._render_element(element_def, {"ds1": df})

        assert result["type"] == "crosstab"
        # pivot produces one record per (region, Total) combination
        by_region = {row["region"]: row for row in result["data"]}
        assert by_region["North"]["A"] == 10
        assert by_region["North"]["B"] == 20
        assert by_region["North"]["Total"] == 30
        assert by_region["South"]["A"] == 30
        assert by_region["South"]["B"] == 40
        assert by_region["South"]["Total"] == 70
        assert by_region["Total"]["A"] == 40
        assert by_region["Total"]["B"] == 60
        assert by_region["Total"]["Total"] == 100

    def test_render_crosstab_aggregation_avg(self):
        renderer = ReportRenderer()
        element_def = {
            "type": "crosstab",
            "data_source": "ds1",
            "rowField": "region",
            "columnField": "product",
            "valueField": "sales",
            "aggregation": "mean",
        }
        df = pd.DataFrame(
            [
                {"region": "North", "product": "A", "sales": 10},
                {"region": "North", "product": "A", "sales": 20},
            ]
        )
        result = renderer._render_element(element_def, {"ds1": df})
        north_a = [r for r in result["data"] if r.get("region") == "North" and "A" in r]
        assert north_a and north_a[0]["A"] == 15.0

    def test_render_image_defaults(self):
        renderer = ReportRenderer()
        element_def = {"type": "image", "source": "/img/logo.png", "label": "Logo"}
        result = renderer._render_element(element_def, {})
        assert result["type"] == "image"
        assert result["source"] == "/img/logo.png"
        assert result["position"] == "left"
        assert result["label"] == "Logo"

    def test_render_unknown_element_type(self):
        renderer = ReportRenderer()
        result = renderer._render_element({"type": "widget"}, {})
        assert result["type"] == "widget"
        assert "error" in result
