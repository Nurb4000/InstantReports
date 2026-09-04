from __future__ import annotations

import io
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


class ChartGenerator:
    """Generate charts using matplotlib for embedding in reports."""

    def generate(self, chart_def: dict[str, Any], data: pd.DataFrame) -> bytes:
        """Generate a chart and return as PNG bytes.

        Args:
            chart_def: Chart definition with type, x_field, y_field, etc.
            data: DataFrame with the data to plot

        Returns:
            PNG image bytes
        """
        chart_type = chart_def.get("chart_type", "bar")
        x_field = chart_def.get("x_field")
        y_field = chart_def.get("y_field")

        if chart_type not in ("bar", "line", "pie", "scatter"):
            raise ValueError(f"Unsupported chart type: {chart_type}")

        fig, ax = plt.subplots(figsize=(10, 6))

        if chart_type == "bar":
            self._plot_bar(ax, data, x_field, y_field)
        elif chart_type == "line":
            self._plot_line(ax, data, x_field, y_field)
        elif chart_type == "pie":
            self._plot_pie(ax, data, x_field, y_field)
        elif chart_type == "scatter":
            self._plot_scatter(ax, data, x_field, y_field)

        ax.set_title(chart_def.get("title", "Chart"))

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return buf.read()

    def _plot_bar(self, ax: plt.Axes, df: pd.DataFrame, x_field: str, y_field: str) -> None:
        """Plot a bar chart."""
        if x_field in df.columns and y_field in df.columns:
            ax.bar(df[x_field], df[y_field])
            ax.set_xlabel(x_field)
            ax.set_ylabel(y_field)

    def _plot_line(self, ax: plt.Axes, df: pd.DataFrame, x_field: str, y_field: str) -> None:
        """Plot a line chart."""
        if x_field in df.columns and y_field in df.columns:
            ax.plot(df[x_field], df[y_field], marker="o")
            ax.set_xlabel(x_field)
            ax.set_ylabel(y_field)

    def _plot_pie(self, ax: plt.Axes, df: pd.DataFrame, x_field: str, y_field: str) -> None:
        """Plot a pie chart."""
        if x_field in df.columns and y_field in df.columns:
            ax.pie(df[y_field], labels=df[x_field], autopct="%1.1f%%")

    def _plot_scatter(self, ax: plt.Axes, df: pd.DataFrame, x_field: str, y_field: str) -> None:
        """Plot a scatter chart."""
        if x_field in df.columns and y_field in df.columns:
            ax.scatter(df[x_field], df[y_field])
            ax.set_xlabel(x_field)
            ax.set_ylabel(y_field)
