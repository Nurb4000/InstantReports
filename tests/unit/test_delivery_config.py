"""Tests for delivery config builder and redactor."""
from __future__ import annotations

from app.services.delivery.config import build_delivery_config, redact_delivery_config


class TestBuildDeliveryConfig:
    def test_email_type_includes_emails_list(self):
        config = build_delivery_config("email", emails="a@b.com, c@d.com")
        assert config == {"type": "email", "emails": ["a@b.com", "c@d.com"]}

    def test_email_type_handles_empty_emails(self):
        config = build_delivery_config("email", emails="")
        assert config == {"type": "email", "emails": []}

    def test_sftp_type_includes_host_and_defaults_port(self):
        config = build_delivery_config("sftp", sftp_host="files.example.com", sftp_username="u")
        assert config["type"] == "sftp"
        assert config["host"] == "files.example.com"
        assert config["port"] == 22
        assert config["username"] == "u"
        assert config["password"] == ""
        assert config["remote_path"] == "/"

    def test_sftp_type_parses_port(self):
        config = build_delivery_config("sftp", sftp_port="2222")
        assert config["port"] == 2222

    def test_smb_type_includes_server_and_share(self):
        config = build_delivery_config("smb", smb_server="server", smb_share="share")
        assert config["type"] == "smb"
        assert config["server"] == "server"
        assert config["share"] == "share"

    def test_webhook_type_includes_url_and_secret(self):
        config = build_delivery_config("webhook", webhook_url="https://example.com/hook", webhook_secret="s3cret")
        assert config["type"] == "webhook"
        assert config["url"] == "https://example.com/hook"
        assert config["secret"] == "s3cret"

    def test_unknown_type_returns_type_only(self):
        config = build_delivery_config("unknown")
        assert config == {"type": "unknown"}


class TestRedactDeliveryConfig:
    def test_redacts_password_and_secret(self):
        config = {
            "type": "sftp",
            "host": "example.com",
            "password": "super-secret",
            "username": "user",
        }
        redacted = redact_delivery_config(config)
        assert redacted["password"] == "***REDACTED***"
        assert redacted["host"] == "example.com"
        assert redacted["username"] == "user"

    def test_redacts_nested_dicts(self):
        config = {"type": "webhook", "nested": {"secret": "abc", "url": "http://x"}}
        redacted = redact_delivery_config(config)
        assert redacted["nested"]["secret"] == "***REDACTED***"
        assert redacted["nested"]["url"] == "http://x"

    def test_redacts_lists(self):
        config = {"type": "email", "emails": ["a@b.com"], "nested": [{"password": "x"}]}
        redacted = redact_delivery_config(config)
        assert redacted["nested"][0]["password"] == "***REDACTED***"

    def test_none_and_empty_pass_through(self):
        assert redact_delivery_config(None) is None
        assert redact_delivery_config({}) == {}
