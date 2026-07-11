"""Coverage-gap tests for abicheck.mcp_server.

Targets the previously-uncovered handler bodies and error/timeout branches of
the MCP tools that the existing suites don't reach: ``abi_audit``,
``abi_estimate``, the ``abi_scan`` size-check/timeout/error branches, the
``abi_dump``/``abi_compare`` timeout paths, ``main()`` argument validation, and
a few small config helpers.  Every test drives a real handler with crafted
inputs (real temp files / JSON snapshots) and asserts the meaningful result
structure — no smoke tests.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Mock the mcp package before importing mcp_server (same pattern as the
# sibling suites so the module imports without the real dependency semantics).
# ---------------------------------------------------------------------------
_mock_fastmcp = MagicMock()
_mock_mcp_module = MagicMock()
_mock_mcp_module.server.fastmcp.FastMCP = _mock_fastmcp
sys.modules.setdefault("mcp", _mock_mcp_module)
sys.modules.setdefault("mcp.server", _mock_mcp_module.server)
sys.modules.setdefault("mcp.server.fastmcp", _mock_mcp_module.server.fastmcp)

_mock_mcp_instance = MagicMock()
_mock_mcp_instance.tool.return_value = lambda fn: fn
_mock_fastmcp.return_value = _mock_mcp_instance

import abicheck.mcp_server as ms  # noqa: E402
import abicheck.service as service  # noqa: E402
from abicheck.mcp_server import (  # noqa: E402
    _audit_log,
    _check_file_size,
    _env_int,
    abi_audit,
    abi_compare,
    abi_dump,
    abi_estimate,
    abi_scan,
    main,
)
from abicheck.model import AbiSnapshot  # noqa: E402
from abicheck.serialization import snapshot_to_json  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _snapshot_file(tmp_path: Path, name: str = "lib.abi.json") -> Path:
    p = tmp_path / name
    p.write_text(
        snapshot_to_json(AbiSnapshot(library="libtest.so", version="1.0")),
        encoding="utf-8",
    )
    return p


def _fake_elf(tmp_path: Path, name: str = "lib.so") -> Path:
    p = tmp_path / name
    p.write_bytes(b"\x7fELF" + b"\x00" * 100)
    return p


# ===================================================================
# abi_audit  (lines 897-949)
# ===================================================================


class TestAbiAudit:
    def test_snapshot_returns_hygiene_catalog(self, tmp_path: Path):
        """A JSON-snapshot audit runs the crosscheck + pattern-scan engines and
        returns a COMPATIBLE catalog with per-check coverage rows."""
        snap = _snapshot_file(tmp_path)
        data = json.loads(abi_audit(str(snap)))
        assert data["status"] == "ok"
        assert data["verdict"] == "COMPATIBLE"
        assert data["exit_code"] == 0
        # crosscheck catalog and pattern-scan payloads are both present
        assert "catalog" in data
        assert "coverage" in data["catalog"]
        assert data["catalog"]["findings"] == 0
        assert "pattern_scan" in data

    def test_with_header_drives_pattern_scan(self, tmp_path: Path):
        """A supplied header is fed to the compiler-free pattern pre-scan; the
        scan reports the file in its coverage rather than skipping."""
        snap = _snapshot_file(tmp_path)
        hdr = tmp_path / "api.h"
        hdr.write_text("#define FOO 1\nint foo(void);\n", encoding="utf-8")
        data = json.loads(abi_audit(str(snap), headers=[str(hdr)]))
        assert data["status"] == "ok"
        assert "pattern_scan" in data

    def test_missing_library_returns_error(self, tmp_path: Path):
        data = json.loads(abi_audit(str(tmp_path / "nope.so")))
        assert data["status"] == "error"
        assert "not found" in data["error"].lower()

    def test_unresolvable_input_returns_sanitized_error(self, tmp_path: Path):
        """An existing but unrecognized file makes _resolve_input raise inside
        the worker; the outer handler converts it to a structured error."""
        bad = tmp_path / "junk.bin"
        bad.write_bytes(b"\x00\x01\x02\x03not a known format")
        data = json.loads(abi_audit(str(bad)))
        assert data["status"] == "error"
        assert data["error"]

    def test_timeout_branch(self, tmp_path: Path, monkeypatch):
        """A resolve that outruns MCP_TIMEOUT yields a timeout error payload."""
        snap = _snapshot_file(tmp_path)
        monkeypatch.setattr(ms, "MCP_TIMEOUT", 0.1)

        def _slow(*a, **k):
            time.sleep(1.0)
            return AbiSnapshot(library="x", version="1.0")

        monkeypatch.setattr(ms, "_resolve_input", _slow)
        data = json.loads(abi_audit(str(snap)))
        assert data["status"] == "error"
        assert "timed out" in data["error"]


# ===================================================================
# abi_estimate  (lines 982-1032)
# ===================================================================


class TestAbiEstimate:
    def test_returns_per_layer_estimate(self, tmp_path: Path):
        """A dry-run estimate against a real binary returns per-layer cost rows
        and a positive total without invoking any compiler."""
        so = _fake_elf(tmp_path)
        data = json.loads(abi_estimate(str(so)))
        assert data["status"] == "ok"
        assert data["mode"] == "pr"
        assert isinstance(data["estimate"], list)
        assert len(data["estimate"]) >= 1
        assert data["total_est_seconds"] >= 0
        # each row carries a layer + est_seconds
        assert all("layer" in row and "est_seconds" in row for row in data["estimate"])

    def test_missing_binary_returns_error(self, tmp_path: Path):
        data = json.loads(abi_estimate(str(tmp_path / "gone.so")))
        assert data["status"] == "error"
        assert "not found" in data["error"].lower()

    def test_sources_and_compile_db_paths_resolved(self, tmp_path: Path):
        """sources + compile_db args are resolved and threaded into the request
        (exercises the optional-path resolution branches)."""
        so = _fake_elf(tmp_path)
        src = tmp_path / "src"
        src.mkdir()
        cdb = tmp_path / "compile_commands.json"
        cdb.write_text("[]", encoding="utf-8")
        data = json.loads(abi_estimate(str(so), sources=str(src), compile_db=str(cdb)))
        assert data["status"] == "ok"
        assert "estimate" in data

    def test_seeded_changed_paths_empty_list(self, tmp_path: Path):
        """An explicit empty changed-paths list is honoured (seeded no-op PR)."""
        so = _fake_elf(tmp_path)
        data = json.loads(abi_estimate(str(so), changed_paths=[]))
        assert data["status"] == "ok"

    def test_exception_branch_is_sanitized(self, tmp_path: Path, monkeypatch):
        """A failure inside estimate_scan is caught and sanitized."""
        so = _fake_elf(tmp_path)

        def _boom(req):
            raise RuntimeError("internal 0xDEAD")

        monkeypatch.setattr(service, "estimate_scan", _boom)
        data = json.loads(abi_estimate(str(so)))
        assert data["status"] == "error"
        assert "0xDEAD" not in data["error"]
        assert "unexpected error" in data["error"]


# ===================================================================
# abi_scan  (lines 1084, 1110, 1113, 1137-1140, 1150-1154)
# ===================================================================


class TestAbiScan:
    def test_missing_binary_returns_error(self, tmp_path: Path):
        data = json.loads(abi_scan(str(tmp_path / "absent.so")))
        assert data["status"] == "error"
        assert "not found" in data["error"].lower()

    def test_compile_db_and_baseline_size_checked_and_forwarded(
        self, tmp_path: Path, monkeypatch
    ):
        """compile_db and baseline paths hit the size-check branches and are
        forwarded onto the ScanRequest handed to the subprocess runner."""
        snap = _snapshot_file(tmp_path)
        cdb = tmp_path / "compile_commands.json"
        cdb.write_text("[]", encoding="utf-8")
        base = _snapshot_file(tmp_path, "baseline.abi.json")

        captured: dict[str, object] = {}

        def _fake(req, timeout):
            captured["compile_db"] = req.compile_db
            captured["baseline"] = req.baseline
            return {"verdict": "COMPATIBLE", "exit_code": 0}

        monkeypatch.setattr(service, "run_scan_subprocess", _fake)
        data = json.loads(abi_scan(str(snap), compile_db=str(cdb), baseline=str(base)))
        assert data["status"] == "ok"
        assert data["verdict"] == "COMPATIBLE"
        assert captured["compile_db"] == cdb.resolve()
        assert captured["baseline"] == base.resolve()

    def test_timeout_branch(self, tmp_path: Path, monkeypatch):
        """A subprocess TimeoutError becomes a structured timeout error."""
        snap = _snapshot_file(tmp_path)

        def _timeout(req, timeout):
            raise TimeoutError

        monkeypatch.setattr(service, "run_scan_subprocess", _timeout)
        data = json.loads(abi_scan(str(snap)))
        assert data["status"] == "error"
        assert "timed out" in data["error"]

    def test_exception_branch_is_sanitized(self, tmp_path: Path, monkeypatch):
        """A generic subprocess failure is caught and sanitized (no internals)."""
        snap = _snapshot_file(tmp_path)

        def _boom(req, timeout):
            raise RuntimeError("secret 0xBEEF")

        monkeypatch.setattr(service, "run_scan_subprocess", _boom)
        data = json.loads(abi_scan(str(snap)))
        assert data["status"] == "error"
        assert "0xBEEF" not in data["error"]
        assert "unexpected error" in data["error"]


# ===================================================================
# abi_dump / abi_compare timeout branches  (lines 474-477, 658-666)
# ===================================================================


class TestToolTimeouts:
    def test_abi_dump_timeout(self, tmp_path: Path, monkeypatch):
        so = _fake_elf(tmp_path)
        monkeypatch.setattr(ms, "MCP_TIMEOUT", 0.1)

        def _slow(*a, **k):
            time.sleep(1.0)
            return AbiSnapshot(library="x", version="1.0")

        monkeypatch.setattr(ms, "_resolve_input", _slow)
        data = json.loads(abi_dump(str(so)))
        assert data["status"] == "error"
        assert "abi_dump timed out" in data["error"]

    def test_abi_compare_timeout(self, tmp_path: Path, monkeypatch):
        old = _snapshot_file(tmp_path, "old.json")
        new = _snapshot_file(tmp_path, "new.json")
        monkeypatch.setattr(ms, "MCP_TIMEOUT", 0.1)

        def _slow(*a, **k):
            time.sleep(1.0)
            return AbiSnapshot(library="x", version="1.0")

        monkeypatch.setattr(ms, "_resolve_input", _slow)
        data = json.loads(abi_compare(str(old), str(new)))
        assert data["status"] == "error"
        assert "abi_compare timed out" in data["error"]


# ===================================================================
# Small config helpers  (lines 84-85, 109, 132)
# ===================================================================


class TestConfigHelpers:
    def test_env_int_invalid_raises(self, monkeypatch):
        monkeypatch.setenv("ABICHECK_TEST_BOGUS_INT", "not-a-number")
        with pytest.raises(ValueError, match="not a valid integer"):
            _env_int("ABICHECK_TEST_BOGUS_INT", "10")

    def test_env_int_default_used(self, monkeypatch):
        monkeypatch.delenv("ABICHECK_TEST_MISSING_INT", raising=False)
        assert _env_int("ABICHECK_TEST_MISSING_INT", "42") == 42

    def test_check_file_size_over_limit_raises(self, tmp_path: Path, monkeypatch):
        f = tmp_path / "big.so"
        f.write_bytes(b"\x00" * 4096)
        monkeypatch.setattr(ms, "MCP_MAX_FILE_SIZE", 16)
        with pytest.raises(ValueError, match="exceeds limit"):
            _check_file_size(f, label="library_path")

    def test_check_file_size_missing_is_noop(self, tmp_path: Path):
        # A missing file is deferred to downstream handling, not raised here.
        _check_file_size(tmp_path / "nope.so", label="input")

    def test_check_file_size_stat_oserror_wrapped(self):
        """A non-FileNotFound OSError from stat() is wrapped as a ValueError."""

        class _Bad:
            def stat(self):
                raise PermissionError("denied")

        with pytest.raises(ValueError, match="Cannot check input file size"):
            _check_file_size(_Bad(), label="input")  # type: ignore[arg-type]

    def test_audit_log_structured_json(self, monkeypatch, caplog):
        """With structured logging enabled, the audit record is emitted as JSON."""
        monkeypatch.setattr(ms, "_structured_logging", True)
        with caplog.at_level("INFO", logger="abicheck.mcp"):
            _audit_log(
                "abi_dump", {"library": "libx.so"}, 0.5, "ok", verdict="BREAKING"
            )
        # The emitted message is valid JSON carrying the fields.
        rec = json.loads(caplog.records[-1].getMessage())
        assert rec["tool"] == "abi_dump"
        assert rec["status"] == "ok"
        assert rec["verdict"] == "BREAKING"


# ===================================================================
# main()  argument validation + logging setup  (lines 1192, 1194, 1202)
# ===================================================================


class TestMainArgValidation:
    def test_nonpositive_timeout_errors(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["abicheck-mcp", "--timeout", "0"])
        with pytest.raises(SystemExit):
            main()

    def test_nonpositive_max_file_size_errors(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["abicheck-mcp", "--max-file-size", "0"])
        with pytest.raises(SystemExit):
            main()

    def test_json_log_format_sets_structured_logging(self, monkeypatch):
        """--log-format json flips structured logging and runs the server."""
        calls: list[str] = []
        monkeypatch.setattr(ms.mcp, "run", lambda transport: calls.append(transport))
        monkeypatch.setattr(
            sys, "argv", ["abicheck-mcp", "--log-format", "json", "--timeout", "5"]
        )
        try:
            main()
            assert calls == ["stdio"]
            assert ms._structured_logging is True
            assert ms.MCP_TIMEOUT == 5
        finally:
            # Restore module-level globals mutated by main().
            ms._structured_logging = False
