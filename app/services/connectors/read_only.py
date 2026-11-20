"""Read-only query enforcement for SQL database connectors (feature-gap #7).

Every SQL connector (PostgreSQL, MySQL, SQL Server, ODBC) executes a user- or
designer-supplied query string against the connected database using the
connection's stored credentials. Without a guard, a report definition could carry
an ``INSERT``/``UPDATE``/``DELETE``/``DROP`` statement and mutate or destroy data
through that connection. These helpers restrict a query to a single read-only
``SELECT`` before it is handed to the driver -- the driver-level counterpart to
the Excel connector's pandasql validation (M2), applied uniformly to all SQL
backends so no database driver can be used as an unguarded write path.
"""

from __future__ import annotations

import re


class ReadOnlyQueryError(ValueError):
    """Raised when a query is not a single read-only SELECT."""


# DML/DDL/control keywords that make a statement non-read-only or unsafe. Matched
# as whole words so they cannot be smuggled in via e.g. "SELECT ... UPDATE ...".
# A reserved word used verbatim as a column/table name can cause a false positive;
# rejecting it is the safe trade-off for a write-guard.
_DANGEROUS_KEYWORDS = (
    "INSERT", "UPDATE", "DELETE", "REPLACE", "MERGE",
    "CREATE", "ALTER", "DROP", "TRUNCATE", "RENAME",
    "EXEC", "EXECUTE", "CALL",
    "GRANT", "REVOKE",
    "SET", "USE", "BEGIN", "COMMIT", "ROLLBACK", "TRAN",
    "LOCK", "UNLOCK",
)


def validate_read_only_query(query: str) -> None:
    """Ensure ``query`` is a single read-only SELECT.

    Raises :class:`ReadOnlyQueryError` on an empty query, SQL comments, multiple
    statements, a non-SELECT first keyword, or any disallowed DML/DDL keyword.
    """
    if not query or not query.strip():
        raise ReadOnlyQueryError("Query must not be empty")

    normalized = re.sub(r"\s+", " ", query).strip()

    # Comments can obfuscate dangerous keywords; reject them outright rather than
    # risk imperfect stripping.
    if "--" in normalized or "/*" in normalized or "#" in normalized:
        raise ReadOnlyQueryError("Query must not contain SQL comments")

    if ";" in normalized:
        raise ReadOnlyQueryError(
            "Query must be a single statement; multiple statements are not allowed"
        )

    first = normalized.split(None, 1)[0].upper()
    if first != "SELECT":
        raise ReadOnlyQueryError(f"Query must be a SELECT statement, got: {first!r}")

    upper = normalized.upper()
    for kw in _DANGEROUS_KEYWORDS:
        if re.search(r"\b" + kw + r"\b", upper):
            raise ReadOnlyQueryError(f"Query contains disallowed keyword: {kw}")
