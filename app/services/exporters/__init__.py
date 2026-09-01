from app.services.exporters.excel_csv_html import (
    CSVExporter,
    ExcelExporter,
    HTMLExporter,
)

__all__ = [
    "CSVExporter",
    "ExcelExporter",
    "HTMLExporter",
    "PDFExporter",
]


def __getattr__(name):
    # Lazily import PDFExporter so the package can be imported (and its
    # HTML/CSV exporters tested) without reportlab installed.
    if name == "PDFExporter":
        from app.services.exporters.pdf import PDFExporter

        return PDFExporter

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
