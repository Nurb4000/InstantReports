"""Regression guards for DOM-XSS hardening in templates (M2).

The admin/designer templates build rows with ``innerHTML`` from server-supplied,
attacker-controllable values (schedule/report/user names, commit messages, audit
details, AI responses). These tests assert the shared ``escapeHtml`` helper is
loaded and actually applied at the previously-vulnerable call sites.
"""

from __future__ import annotations

from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
TEMPLATES = BASE / "templates"
STATIC = BASE / "static"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_base_loads_shared_escape_helper():
    base = _read(TEMPLATES / "base.html")
    assert "js/escape.js" in base


def test_escape_helper_escapes_dangerous_characters():
    js = _read(STATIC / "js" / "escape.js")
    for replacement in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"):
        assert replacement in js


def test_admin_templates_escape_untrusted_fields():
    assert "escapeHtml(schedule.name)" in _read(TEMPLATES / "admin" / "schedules.html")
    audit = _read(TEMPLATES / "admin" / "audit.html")
    assert "escapeHtml(log.action)" in audit
    assert "escapeHtml(JSON.stringify(log.details" in audit
    assert "escapeHtml(user.name)" in _read(TEMPLATES / "admin" / "users.html")


def test_designer_templates_escape_untrusted_fields():
    editor = _read(TEMPLATES / "designer" / "editor.html")
    assert "escapeHtml(v.commit_message)" in editor
    assert "escapeHtml(conn.name)" in editor
    assert "this.escapeHtml(msg.content)" in _read(
        TEMPLATES / "designer" / "ai_chat.html"
    )
