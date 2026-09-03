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
    "export_report",
    "get_exporter",
    "get_file_extension",
    "get_mime_type",
    "normalize_output_format",
]

# Canonical supported formats (also the values the schedule form emits).
SUPPORTED_FORMATS = ("pdf", "xlsx", "csv", "html")

# Accept common aliases / MIME content-types and normalize to a supported format.
_FORMAT_ALIASES = {
    "pdf": "pdf",
    "xlsx": "xlsx",
    "excel": "xlsx",
    "xls": "xlsx",
    "csv": "csv",
    "text/csv": "csv",
    "html": "html",
    "htm": "html",
    "text/html": "html",
}

_MIME_TYPES = {
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
    "html": "text/html; charset=utf-8",
}

_FILE_EXTENSIONS = {
    "pdf": "pdf",
    "xlsx": "xlsx",
    "csv": "csv",
    "html": "html",
}


def __getattr__(name):
    # Lazily import PDFExporter so the package can be imported (and its
    # HTML/CSV exporters tested) without reportlab installed.
    if name == "PDFExporter":
        from app.services.exporters.pdf import PDFExporter

        return PDFExporter

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def normalize_output_format(output_format: str | None) -> str:
    """Normalize a format string to one of the supported formats.

    Raises ``ValueError`` for unknown formats so a bad value fails loudly at the
    API boundary instead of silently defaulting to PDF.
    """
    key = (output_format or "pdf").strip().lower()
    key = _FORMAT_ALIASES.get(key, key)
    if key not in SUPPORTED_FORMATS:
        raise ValueError(f"Unsupported output format: {output_format!r}")
    return key


def get_exporter(output_format: str | None = "pdf"):
    """Return an exporter instance for the given output format.

    PDF is imported lazily so this function is callable in environments without
    reportlab for the other three formats.
    """
    fmt = normalize_output_format(output_format)
    if fmt == "pdf":
        from app.services.exporters.pdf import PDFExporter

        return PDFExporter()
    if fmt == "xlsx":
        return ExcelExporter()
    if fmt == "csv":
        return CSVExporter()
    return HTMLExporter()  # html


def export_report(rendered_report: dict, output_format: str | None = "pdf") -> bytes:
    """Render -> export in one call, returning file bytes.

    Normalizes str-returning exporters (HTML) to bytes so callers can always
    store the result in ``ReportOutput.file_data`` (a BYTEA column).
    """
    result = get_exporter(output_format).export(rendered_report)
    if isinstance(result, str):
        result = result.encode("utf-8")
    return result


def get_mime_type(output_format: str | None = "pdf") -> str:
    return _MIME_TYPES[normalize_output_format(output_format)]


def get_file_extension(output_format: str | None = "pdf") -> str:
    return _FILE_EXTENSIONS[normalize_output_format(output_format)]
