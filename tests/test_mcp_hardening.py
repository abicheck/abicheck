"""Tests for MCP auth hardening: file size limits, audit logging (2c, 2d)."""

import json
import logging

import pytest

try:
    import abicheck.mcp_shared as ms

    _has_mcp = True
except ImportError:
    _has_mcp = False

pytestmark = pytest.mark.skipif(not _has_mcp, reason="MCP dependencies not installed")


class TestCheckFileSize:
    def test_small_file_passes(self, tmp_path):
        p = tmp_path / "small.so"
        p.write_bytes(b"x" * 100)
        ms._check_file_size(p)  # should not raise

    def test_large_file_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ms, "MCP_MAX_FILE_SIZE", 50)
        p = tmp_path / "big.so"
        p.write_bytes(b"x" * 100)
        with pytest.raises(ValueError, match="exceeds limit"):
            ms._check_file_size(p)

    def test_exact_limit_passes(self, tmp_path, monkeypatch):
        """File at exactly the limit should pass (strictly greater triggers)."""
        monkeypatch.setattr(ms, "MCP_MAX_FILE_SIZE", 100)
        p = tmp_path / "exact.so"
        p.write_bytes(b"x" * 100)
        ms._check_file_size(p)  # should not raise

    def test_missing_file_no_error(self, tmp_path):
        result = ms._check_file_size(tmp_path / "nonexistent.so")
        assert result is None


class TestAuditLog:
    def test_text_format(self, caplog):
        with caplog.at_level(logging.INFO, logger="abicheck.mcp"):
            ms._audit_log("abi_dump", {"library": "foo.so"}, 1.234, "ok")
        assert "tool=abi_dump" in caplog.text
        assert "duration=1.234s" in caplog.text
        assert "status=ok" in caplog.text

    def test_json_format(self, caplog, monkeypatch):
        monkeypatch.setattr(ms, "_structured_logging", True)
        with caplog.at_level(logging.INFO, logger="abicheck.mcp"):
            ms._audit_log(
                "abi_compare",
                {"old": "a.so", "new": "b.so"},
                2.5,
                "ok",
                verdict="NO_CHANGE",
            )
        # caplog.records[].message contains just the formatted message (no prefix)
        assert len(caplog.records) == 1
        record = json.loads(caplog.records[0].message)
        assert record["tool"] == "abi_compare"
        assert record["verdict"] == "NO_CHANGE"

    def test_verdict_included_when_present(self, caplog):
        with caplog.at_level(logging.INFO, logger="abicheck.mcp"):
            ms._audit_log("abi_compare", {"old": "a.so"}, 1.0, "ok", verdict="BREAKING")
        assert "verdict=BREAKING" in caplog.text

    def test_verdict_none_omitted(self, caplog):
        with caplog.at_level(logging.INFO, logger="abicheck.mcp"):
            ms._audit_log("abi_dump", {"library": "foo.so"}, 1.0, "ok", verdict=None)
        assert "verdict" not in caplog.text


class TestCheckFileSizeOSError:
    def test_oserror_other_than_fnf_raises(self, tmp_path):
        """Non-FileNotFoundError OSError should propagate as ValueError."""
        from unittest import mock

        p = tmp_path / "perm.so"
        p.write_bytes(b"x")
        with mock.patch.object(type(p), "stat", side_effect=PermissionError("denied")):
            with pytest.raises(ValueError, match="Cannot check"):
                ms._check_file_size(p)

    def test_oserror_message_does_not_leak_the_path(self, tmp_path):
        # A real OS-raised OSError carries the filename as a constructor arg
        # (errno, strerror, filename), and str(exc) embeds it -- ValueError
        # messages are surfaced verbatim by _sanitize_error, so this would
        # leak the full path to an MCP client if not stripped (Codex review).
        from unittest import mock

        p = tmp_path / "private" / "config.yml"
        real_error = PermissionError(13, "Permission denied", str(p))
        with mock.patch.object(type(p), "stat", side_effect=real_error):
            with pytest.raises(ValueError) as excinfo:
                ms._check_file_size(p)
        assert str(p) not in str(excinfo.value)
        assert "Permission denied" in str(excinfo.value)


class TestEnvInt:
    def test_valid_env_int(self):
        result = ms._env_int("TEST_UNUSED_VAR", "42")
        assert result == 42

    def test_invalid_env_int_raises(self, monkeypatch):
        monkeypatch.setenv("ABICHECK_TEST_BAD", "abc")
        with pytest.raises(ValueError, match="not a valid integer"):
            ms._env_int("ABICHECK_TEST_BAD", "10")
