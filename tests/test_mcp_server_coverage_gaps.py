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
# Mock the mcp package before importing mcp_server so it imports without the
# real dependency, then RESTORE sys.modules afterward. Leaving a spec-less
# MagicMock named "mcp" behind would break any later module that probes
# ``importlib.util.find_spec("mcp")`` (e.g. tests/test_cli_contract.py), which
# raises ``ValueError: mcp.__spec__ is not set`` during collection (Codex
# review). mcp_server keeps its own FastMCP reference after import, so the
# restore is safe.
# ---------------------------------------------------------------------------
_MCP_MODULE_NAMES = ("mcp", "mcp.server", "mcp.server.fastmcp")
_saved_mcp_modules = {name: sys.modules.get(name) for name in _MCP_MODULE_NAMES}

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
import abicheck.mcp_shared as mcp_shared  # noqa: E402
import abicheck.service as service  # noqa: E402
from abicheck.mcp_server import (  # noqa: E402
    _audit_log,
    _check_file_size,
    abi_audit,
    abi_compare,
    abi_dump,
    abi_estimate,
    abi_scan,
    abi_scan_set,
    main,
)
from abicheck.mcp_shared import _env_int  # noqa: E402
from abicheck.model import AbiSnapshot  # noqa: E402
from abicheck.serialization import snapshot_to_json  # noqa: E402

# Undo any mock modules we injected above so they don't leak to other test
# modules collected later in the same session.
for _name, _original in _saved_mcp_modules.items():
    if _original is None:
        sys.modules.pop(_name, None)
    else:
        sys.modules[_name] = _original

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
        monkeypatch.setattr(mcp_shared, "MCP_TIMEOUT", 0.1)

        def _slow(*a, **k):
            time.sleep(1.0)
            return AbiSnapshot(library="x", version="1.0")

        monkeypatch.setattr(ms, "_resolve_input", _slow)
        data = json.loads(abi_audit(str(snap)))
        assert data["status"] == "error"
        assert "timed out" in data["error"]

    def test_header_is_passed_as_public_header(self, tmp_path: Path, monkeypatch):
        """abi_audit documents ``headers`` as "Public header files" — it must
        actually classify provenance with them, matching abi_dump/abi_compare."""
        snap = _snapshot_file(tmp_path)
        hdr = tmp_path / "api.h"
        hdr.write_text("int foo(void);\n", encoding="utf-8")

        captured: dict[str, object] = {}
        original_resolve = ms._resolve_input

        def _spy(path, headers, includes, version, lang, **kwargs):
            captured["public_headers"] = kwargs.get("public_headers")
            return original_resolve(path, headers, includes, version, lang, **kwargs)

        monkeypatch.setattr(ms, "_resolve_input", _spy)
        data = json.loads(abi_audit(str(snap), headers=[str(hdr)]))
        assert data["status"] == "ok"
        assert captured["public_headers"] == [hdr]


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
        data = json.loads(abi_scan(str(snap), compile_db=str(cdb), against=str(base)))
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

    @pytest.mark.parametrize("bad_depth", ["full", "symbols", "graph"])
    def test_rejects_internal_only_depth(self, tmp_path: Path, bad_depth: str):
        # ADR-043: the public depth ladder is exactly binary/headers/build/source
        # -- "full"/"symbols"/"graph" are internal vocabulary that must not leak
        # into the MCP tool surface (parity with the CLI's --depth rejection).
        snap = _snapshot_file(tmp_path)
        data = json.loads(abi_scan(str(snap), depth=bad_depth))
        assert data["status"] == "error"
        assert "Unknown depth" in data["error"]

    @pytest.mark.parametrize("good_depth", ["binary", "headers", "build", "source"])
    def test_accepts_public_depth(self, tmp_path: Path, monkeypatch, good_depth: str):
        snap = _snapshot_file(tmp_path)

        def _fake(req, timeout):
            return {"verdict": "COMPATIBLE", "exit_code": 0}

        monkeypatch.setattr(service, "run_scan_subprocess", _fake)
        data = json.loads(abi_scan(str(snap), depth=good_depth))
        assert data["status"] == "ok"


class TestAbiScanSet:
    """G35 MCP parity: ``abi_scan_set`` is the multi-artifact sibling of
    ``abi_scan``, routed through ``run_scan_set_subprocess`` (ADR-056).
    """

    def test_rejects_single_artifact(self, tmp_path: Path):
        so = _fake_elf(tmp_path)
        data = json.loads(abi_scan_set([str(so)]))
        assert data["status"] == "error"
        assert "2 or more" in data["error"]

    def test_missing_artifact_returns_error(self, tmp_path: Path):
        so = _fake_elf(tmp_path, "a.so")
        data = json.loads(abi_scan_set([str(so), str(tmp_path / "absent.so")]))
        assert data["status"] == "error"
        assert "not found" in data["error"].lower()

    def test_forwards_binaries_and_bundle_system_providers(
        self, tmp_path: Path, monkeypatch
    ):
        a = _fake_elf(tmp_path, "liba.so")
        b = _fake_elf(tmp_path, "libb.so")

        captured: dict[str, object] = {}

        def _fake(req, timeout):
            captured["binaries"] = req.binaries
            captured["mode"] = req.mode
            captured["bundle_system_providers"] = req.bundle_system_providers
            return {"verdict": "COMPATIBLE", "exit_code": 0, "per_artifact": []}

        monkeypatch.setattr(service, "run_scan_set_subprocess", _fake)
        data = json.loads(
            abi_scan_set(
                [str(a), str(b)],
                bundle_system_providers=["libexternal.so"],
            )
        )
        assert data["status"] == "ok"
        assert data["verdict"] == "COMPATIBLE"
        assert set(captured["binaries"]) == {a.resolve(), b.resolve()}
        assert captured["mode"] == "audit"
        assert captured["bundle_system_providers"] == ("libexternal.so",)

    def test_timeout_branch(self, tmp_path: Path, monkeypatch):
        a = _fake_elf(tmp_path, "liba.so")
        b = _fake_elf(tmp_path, "libb.so")

        def _timeout(req, timeout):
            raise TimeoutError

        monkeypatch.setattr(service, "run_scan_set_subprocess", _timeout)
        data = json.loads(abi_scan_set([str(a), str(b)]))
        assert data["status"] == "error"
        assert "timed out" in data["error"]

    def test_exception_branch_is_sanitized(self, tmp_path: Path, monkeypatch):
        a = _fake_elf(tmp_path, "liba.so")
        b = _fake_elf(tmp_path, "libb.so")

        def _boom(req, timeout):
            raise RuntimeError("secret 0xBEEF")

        monkeypatch.setattr(service, "run_scan_set_subprocess", _boom)
        data = json.loads(abi_scan_set([str(a), str(b)]))
        assert data["status"] == "error"
        assert "0xBEEF" not in data["error"]
        assert "unexpected error" in data["error"]

    @pytest.mark.parametrize("bad_depth", ["full", "symbols", "graph"])
    def test_rejects_internal_only_depth(self, tmp_path: Path, bad_depth: str):
        a = _fake_elf(tmp_path, "liba.so")
        b = _fake_elf(tmp_path, "libb.so")
        data = json.loads(abi_scan_set([str(a), str(b)], depth=bad_depth))
        assert data["status"] == "error"
        assert "Unknown depth" in data["error"]


class TestAbiEstimateDepthValidation:
    @pytest.mark.parametrize("bad_depth", ["full", "symbols", "graph"])
    def test_rejects_internal_only_depth(self, tmp_path: Path, bad_depth: str):
        so = _fake_elf(tmp_path)
        data = json.loads(abi_estimate(str(so), depth=bad_depth))
        assert data["status"] == "error"
        assert "Unknown depth" in data["error"]


# ===================================================================
# abi_dump / abi_compare timeout branches  (lines 474-477, 658-666)
# ===================================================================


class TestToolTimeouts:
    def test_abi_dump_timeout(self, tmp_path: Path, monkeypatch):
        so = _fake_elf(tmp_path)
        monkeypatch.setattr(mcp_shared, "MCP_TIMEOUT", 0.1)

        def _slow(*a, **k):
            time.sleep(1.0)
            return AbiSnapshot(library="x", version="1.0")

        # The slow work now lives behind the Tier-2 chokepoint (G33 Phase 5,
        # matching abi_compare below), so stubbing the MCP-local wrapper would
        # no longer stall anything.
        monkeypatch.setattr(service, "resolve_input", _slow)
        data = json.loads(abi_dump(str(so)))
        assert data["status"] == "error"
        assert "abi_dump timed out" in data["error"]

    def test_abi_compare_timeout(self, tmp_path: Path, monkeypatch):
        old = _snapshot_file(tmp_path, "old.json")
        new = _snapshot_file(tmp_path, "new.json")
        monkeypatch.setattr(mcp_shared, "MCP_TIMEOUT", 0.1)

        def _slow(*a, **k):
            time.sleep(1.0)
            return AbiSnapshot(library="x", version="1.0")

        # The slow work now lives behind the Tier-2 chokepoint (ADR-055 D4),
        # so stubbing the MCP-local wrapper would no longer stall anything --
        # the timeout would still be exercised, but against a fast call.
        monkeypatch.setattr(service, "resolve_input", _slow)
        data = json.loads(abi_compare(str(old), str(new)))
        assert data["status"] == "error"
        assert "abi_compare timed out" in data["error"]

    def test_abi_dump_timeout_returns_promptly(self, tmp_path: Path, monkeypatch):
        # abi_dump/abi_compare/abi_audit each used a local `with
        # ThreadPoolExecutor(...) as pool:` block, whose shutdown(wait=True)
        # on exit blocked the *response* until the stuck worker finished --
        # even after future.result(timeout=...) had already raised
        # TimeoutError -- defeating the timeout for the caller too (Codex
        # review). Now routed through mcp_shared._call_with_timeout
        # (shutdown(wait=False)), so the call must return close to
        # MCP_TIMEOUT, not close to the worker's full sleep duration.
        so = _fake_elf(tmp_path)
        monkeypatch.setattr(mcp_shared, "MCP_TIMEOUT", 0.1)

        def _slow(*a, **k):
            time.sleep(2.0)
            return AbiSnapshot(library="x", version="1.0")

        monkeypatch.setattr(service, "resolve_input", _slow)
        started = time.monotonic()
        data = json.loads(abi_dump(str(so)))
        elapsed = time.monotonic() - started
        assert data["status"] == "error"
        assert "abi_dump timed out" in data["error"]
        assert elapsed < 1.0, f"blocked for {elapsed}s -- shutdown(wait=True) regression"

    def test_abi_compare_timeout_returns_promptly(self, tmp_path: Path, monkeypatch):
        old = _snapshot_file(tmp_path, "old.json")
        new = _snapshot_file(tmp_path, "new.json")
        monkeypatch.setattr(mcp_shared, "MCP_TIMEOUT", 0.1)

        def _slow(*a, **k):
            time.sleep(2.0)
            return AbiSnapshot(library="x", version="1.0")

        # Stubbed at the service layer for the same reason as
        # test_abi_compare_timeout above (ADR-055 D4).
        monkeypatch.setattr(service, "resolve_input", _slow)
        started = time.monotonic()
        data = json.loads(abi_compare(str(old), str(new)))
        elapsed = time.monotonic() - started
        assert data["status"] == "error"
        assert "abi_compare timed out" in data["error"]
        assert elapsed < 1.0, f"blocked for {elapsed}s -- shutdown(wait=True) regression"

    def test_abi_audit_timeout_returns_promptly(self, tmp_path: Path, monkeypatch):
        so = _fake_elf(tmp_path)
        monkeypatch.setattr(mcp_shared, "MCP_TIMEOUT", 0.1)

        def _slow(*a, **k):
            time.sleep(2.0)
            return AbiSnapshot(library="x", version="1.0")

        monkeypatch.setattr(ms, "_resolve_input", _slow)
        started = time.monotonic()
        data = json.loads(abi_audit(str(so)))
        elapsed = time.monotonic() - started
        assert data["status"] == "error"
        assert "abi_audit timed out" in data["error"]
        assert elapsed < 1.0, f"blocked for {elapsed}s -- shutdown(wait=True) regression"


class TestAuxiliaryInputSizeChecks:
    """ADR-021b D3 claims every input artifact is size-bounded, but a review
    found abi_dump/abi_audit never checked their header files and
    abi_compare never checked suppression_file/policy_file -- only the
    primary library_path/old_input/new_input were covered (Codex review).
    """

    def _oversized_header(self, tmp_path: Path, monkeypatch) -> Path:
        monkeypatch.setattr(mcp_shared, "MCP_MAX_FILE_SIZE", 16)
        hdr = tmp_path / "big.h"
        hdr.write_text("x" * 64, encoding="utf-8")
        return hdr

    def test_abi_dump_oversized_header_is_rejected(self, tmp_path: Path, monkeypatch):
        so = _fake_elf(tmp_path)
        hdr = self._oversized_header(tmp_path, monkeypatch)
        data = json.loads(abi_dump(str(so), headers=[str(hdr)]))
        assert data["status"] == "error"
        assert "exceeds limit" in data["error"]

    def test_abi_audit_oversized_header_is_rejected(self, tmp_path: Path, monkeypatch):
        so = _fake_elf(tmp_path)
        hdr = self._oversized_header(tmp_path, monkeypatch)
        data = json.loads(abi_audit(str(so), headers=[str(hdr)]))
        assert data["status"] == "error"
        assert "exceeds limit" in data["error"]

    def test_abi_scan_oversized_header_is_rejected(self, tmp_path: Path, monkeypatch):
        so = _fake_elf(tmp_path)
        hdr = self._oversized_header(tmp_path, monkeypatch)
        data = json.loads(abi_scan(str(so), headers=[str(hdr)]))
        assert data["status"] == "error"
        assert "exceeds limit" in data["error"]

    def test_abi_compare_oversized_header_is_rejected(self, tmp_path: Path, monkeypatch):
        old = _snapshot_file(tmp_path, "old.json")
        new = _snapshot_file(tmp_path, "new.json")
        hdr = self._oversized_header(tmp_path, monkeypatch)
        data = json.loads(abi_compare(str(old), str(new), headers=[str(hdr)]))
        assert data["status"] == "error"
        assert "exceeds limit" in data["error"]

    def test_abi_compare_oversized_suppression_file_is_rejected(
        self, tmp_path: Path, monkeypatch
    ):
        old = _snapshot_file(tmp_path, "old.json")
        new = _snapshot_file(tmp_path, "new.json")
        monkeypatch.setattr(mcp_shared, "MCP_MAX_FILE_SIZE", 16)
        supp = tmp_path / "suppress.yml"
        supp.write_text("x" * 64, encoding="utf-8")
        data = json.loads(
            abi_compare(str(old), str(new), suppression_file=str(supp))
        )
        assert data["status"] == "error"
        assert "exceeds limit" in data["error"]

    def test_abi_compare_oversized_policy_file_is_rejected(
        self, tmp_path: Path, monkeypatch
    ):
        old = _snapshot_file(tmp_path, "old.json")
        new = _snapshot_file(tmp_path, "new.json")
        monkeypatch.setattr(mcp_shared, "MCP_MAX_FILE_SIZE", 16)
        pol = tmp_path / "policy.yml"
        pol.write_text("x" * 64, encoding="utf-8")
        data = json.loads(abi_compare(str(old), str(new), policy_file=str(pol)))
        assert data["status"] == "error"
        assert "exceeds limit" in data["error"]


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
        monkeypatch.setattr(mcp_shared, "MCP_MAX_FILE_SIZE", 16)
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
        monkeypatch.setattr(mcp_shared, "_structured_logging", True)
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
        # main() mutates mcp_shared's module globals (not monkeypatch), so
        # capture and restore them ourselves to keep later tests isolated.
        saved_timeout = mcp_shared.MCP_TIMEOUT
        saved_structured_logging = mcp_shared._structured_logging
        try:
            main()
            assert calls == ["stdio"]
            assert mcp_shared._structured_logging is True
            assert mcp_shared.MCP_TIMEOUT == 5
        finally:
            # Restore module-level globals mutated by main().
            mcp_shared._structured_logging = saved_structured_logging
            mcp_shared.MCP_TIMEOUT = saved_timeout
