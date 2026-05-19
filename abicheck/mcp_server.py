# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""MCP (Model Context Protocol) server for abicheck.

Exposes abicheck functionality as MCP tools so that AI agents (Claude Code,
Cursor, OpenAI Agents, etc.) can discover and invoke ABI checking operations
with structured inputs and outputs.

Run as:
    abicheck-mcp          # stdio transport (default)
    python -m abicheck.mcp_server
"""

from __future__ import annotations

import concurrent.futures as _futures
import json
import logging
import os as _os
import platform
import sys
import time as _time
from pathlib import Path
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as _exc:
    _msg = (
        "MCP support requires the 'mcp' package. "
        "Install it with: pip install abicheck[mcp]"
    )
    raise ImportError(_msg) from _exc
except Exception as _exc:  # noqa: BLE001
    # Guard against partial installs or other init-time failures from mcp internals
    raise ImportError(
        f"Failed to initialise MCP support: {_exc}. "
        "Try: pip install --upgrade 'abicheck[mcp]'"
    ) from _exc

from .checker import DiffResult, compare
from .checker_policy import (
    API_BREAK_KINDS,
    BREAKING_KINDS,
    COMPATIBLE_KINDS,
    RISK_KINDS,
    VALID_BASE_POLICIES,
    ChangeKind,
    Verdict,
    impact_for,
    policy_for,
    policy_kind_sets,
)
from .errors import AbicheckError
from .model import AbiSnapshot, Visibility
from .reporter import to_json, to_markdown
from .serialization import load_snapshot, snapshot_to_json

_logger = logging.getLogger("abicheck.mcp")

# ---------------------------------------------------------------------------
# Configuration (environment variables or CLI flags)
# ---------------------------------------------------------------------------

def _env_int(name: str, default: str) -> int:
    """Parse an integer environment variable with a clear error on bad input."""
    raw = _os.environ.get(name, default)
    try:
        return int(raw)
    except ValueError:
        raise ValueError(
            f"Environment variable {name}={raw!r} is not a valid integer"
        ) from None


#: Maximum seconds for a single tool invocation (abi_dump / abi_compare).
MCP_TIMEOUT: int = _env_int("ABICHECK_MCP_TIMEOUT", "120")

#: Maximum input file size in bytes (default 500 MB).
MCP_MAX_FILE_SIZE: int = _env_int("ABICHECK_MCP_MAX_FILE_SIZE", str(500 * 1024 * 1024))

#: Structured JSON log format flag (set via --log-format json).
_structured_logging: bool = False


def _check_file_size(path: Path, *, label: str = "input") -> None:
    """Raise ValueError if *path* exceeds MCP_MAX_FILE_SIZE."""
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return  # let downstream handle missing files
    except OSError as exc:
        raise ValueError(f"Cannot check {label} file size: {exc}") from exc
    if size > MCP_MAX_FILE_SIZE:
        raise ValueError(
            f"{label} is {size / (1024 * 1024):.1f} MB, "
            f"exceeds limit of {MCP_MAX_FILE_SIZE / (1024 * 1024):.0f} MB"
        )


def _audit_log(
    tool: str,
    inputs: dict[str, str],
    duration_s: float,
    status: str,
    verdict: str | None = None,
) -> None:
    """Log a tool invocation for audit purposes."""
    record = {
        "tool": tool,
        "inputs": inputs,
        "duration_s": round(duration_s, 3),
        "status": status,
    }
    if verdict is not None:
        record["verdict"] = verdict
    if _structured_logging:
        _logger.info(json.dumps(record))
    else:
        parts = [f"tool={tool}"]
        for k, v in inputs.items():
            parts.append(f"{k}={v}")
        parts.append(f"duration={duration_s:.3f}s")
        parts.append(f"status={status}")
        if verdict is not None:
            parts.append(f"verdict={verdict}")
        _logger.info(" ".join(parts))


# ---------------------------------------------------------------------------
# Path safety helpers
# ---------------------------------------------------------------------------

# Allowed extensions for output files written by abi_dump
_ALLOWED_OUTPUT_SUFFIXES = frozenset({".json"})

# Allowed extensions for input binary files
_ALLOWED_BINARY_SUFFIXES = frozenset({".so", ".dll", ".dylib", ".json", ".dump", ""})


def _safe_read_path(raw: str, *, label: str = "path") -> Path:
    """Resolve and validate a path for reading.

    - Resolves symlinks and `..` components.
    - Does NOT restrict to a specific directory (read paths are user-specified).
    - Returns the resolved Path.

    Raises ValueError with a generic message on obviously bad input.
    """
    if not raw or raw.strip() == "":
        raise ValueError(f"Empty {label} is not allowed")
    try:
        return Path(raw).resolve()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {label}: {exc!s}") from exc


def _safe_write_path(raw: str, *, label: str = "output_path") -> Path:
    """Resolve and validate a path for writing.

    Enforces:
    - Must have an allowed suffix (.json only)
    - Must not be a system-sensitive location

    Raises ValueError on policy violation.
    """
    if not raw or raw.strip() == "":
        raise ValueError(f"Empty {label} is not allowed")

    try:
        p = Path(raw).resolve()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {label}: {exc!s}") from exc

    if p.suffix.lower() not in _ALLOWED_OUTPUT_SUFFIXES:
        raise ValueError(
            f"{label} must have a .json extension, got: {p.suffix!r}"
        )

    # Block writes to sensitive system locations.
    # Use resolved Path objects to handle symlinks (/etc -> /private/etc on macOS)
    # and canonicalize traversal sequences (../../etc bypasses raw-string checks).
    _os = platform.system()
    if _os in ("Linux", "Darwin"):
        sensitive_system_dirs = [
            Path("/etc"), Path("/bin"), Path("/sbin"),
            Path("/usr/bin"), Path("/usr/sbin"),
            Path("/boot"), Path("/sys"), Path("/proc"),
            Path("/dev"),
        ]
        for sys_dir in sensitive_system_dirs:
            try:
                p.relative_to(sys_dir.resolve())
                raise ValueError(
                    f"{label} points to a sensitive system path: {sys_dir}..."
                )
            except ValueError as e:
                if "sensitive system path" in str(e):
                    raise
    elif _os == "Windows":
        p_str = str(p)
        # Normalize NT extended paths so checks also catch forms like:
        #   \\?\C:\Windows\...
        #   \\?\UNC\localhost\c$\Windows\...
        if p_str.startswith("\\\\?\\"):
            p_str = p_str[4:]
            if p_str.upper().startswith("UNC\\"):
                p_str = "\\\\" + p_str[4:]

        norm = p_str.replace("\\", "/").casefold()
        sensitive_prefixes = (
            "c:/windows/",
            "c:/windows/system32/",
            "c:/program files/",
            "c:/program files (x86)/",
            "c:/programdata/",
            "//localhost/c$/windows/",
            "//127.0.0.1/c$/windows/",
        )
        if norm.startswith(sensitive_prefixes):
            raise ValueError(
                f"{label} points to a sensitive system path"
            )

    # Block writes to SSH/credential directories.
    # Resolve both sides to handle symlinks (e.g. ~/.ssh → /private/home/user/.ssh).
    home = Path.home().resolve()
    for sensitive_dir in [(home / ".ssh").resolve(), (home / ".aws").resolve(), (home / ".gnupg").resolve()]:
        try:
            p.relative_to(sensitive_dir)
            raise ValueError(
                f"{label} points to a sensitive credential directory"
            )
        except ValueError as e:
            if "credential" in str(e):
                raise

    return p


def _sanitize_error(exc: Exception, *, context: str = "operation") -> str:
    """Return a safe error message that does not leak filesystem paths or internals."""
    # Known domain errors: safe to surface as-is
    from .errors import AbicheckError
    if isinstance(exc, AbicheckError):
        return str(exc)
    if isinstance(exc, (ValueError, KeyError)):
        return str(exc)
    # OS/IO errors: return generic message, log details internally
    if isinstance(exc, (OSError, FileNotFoundError, PermissionError)):
        _logger.debug("OS error in %s: %s", context, exc, exc_info=True)
        return f"{context} failed: file system error (check logs for details)"
    # All others: generic
    _logger.debug("Unexpected error in %s: %s", context, exc, exc_info=True)
    return f"{context} failed: unexpected error"


try:
    mcp = FastMCP(
        "abicheck",
        instructions=(
            "ABI compatibility checker for C/C++ shared libraries. "
            "Detects breaking changes in .so/.dll/.dylib files before they reach production. "
            "Use abi_compare to diff two library versions, abi_dump to extract ABI snapshots, "
            "abi_list_changes to browse change kinds, and abi_explain_change for detailed explanations."
        ),
    )
except Exception as _exc:  # noqa: BLE001
    raise ImportError(
        f"Failed to initialise MCP support: {_exc}. "
        "Try: pip install --upgrade 'abicheck[mcp]'"
    ) from _exc


# ---------------------------------------------------------------------------
# Helpers — reuse CLI logic without Click dependency
# ---------------------------------------------------------------------------

def _detect_binary_format(path: Path) -> str | None:
    """Detect binary format from magic bytes — single file open."""
    from .binary_utils import detect_binary_format
    return detect_binary_format(path)


def _resolve_input(
    path: Path,
    headers: list[Path],
    includes: list[Path],
    version: str,
    lang: str,
) -> AbiSnapshot:
    """Auto-detect input type and return an AbiSnapshot.

    Mirrors cli._resolve_input but without Click exceptions.
    """
    binary_fmt = _detect_binary_format(path)

    if binary_fmt == "elf":
        from .dumper import dump
        _SUPPORTED_LANGS = ("c", "c++")
        if lang not in _SUPPORTED_LANGS:
            raise ValueError(
                f"Unsupported lang {lang!r}. Must be one of: {', '.join(_SUPPORTED_LANGS)}"
            )
        compiler = "cc" if lang == "c" else "c++"
        return dump(
            so_path=path,
            headers=headers,
            extra_includes=includes,
            version=version,
            compiler=compiler,
            lang=lang if lang == "c" else None,
        )

    if binary_fmt == "pe":
        from .model import Function
        from .pe_metadata import parse_pe_metadata
        pe_meta = parse_pe_metadata(path)
        if not pe_meta.machine:
            raise AbicheckError(
                f"Failed to extract PE metadata from '{path.name}'. "
                "The file may be corrupt or not a valid PE binary."
            )
        if not pe_meta.exports:
            raise AbicheckError(
                f"PE file '{path.name}' has no exports (named or ordinal). "
                "Verify the file is a valid DLL."
            )
        funcs = [
            Function(
                name=(exp.name or f"ordinal:{exp.ordinal}"),
                mangled=(exp.name or f"ordinal:{exp.ordinal}"),
                return_type="?",
                visibility=Visibility.PUBLIC,
                is_extern_c=not (exp.name or "").startswith("?"),
            )
            for exp in pe_meta.exports
        ]
        return AbiSnapshot(
            library=path.name, version=version,
            functions=funcs, pe=pe_meta, platform="pe",
        )

    if binary_fmt == "macho":
        from .macho_metadata import parse_macho_metadata
        from .model import Function
        macho_meta = parse_macho_metadata(path)
        if not macho_meta.exports and not macho_meta.install_name and not macho_meta.dependent_libs:
            raise AbicheckError(
                f"Mach-O file '{path.name}' has no exports or load-command metadata. "
                "Verify the file is a valid dynamic library."
            )
        funcs = [
            Function(
                name=exp.name, mangled=exp.name, return_type="?",
                visibility=Visibility.PUBLIC,
                is_extern_c=not exp.name.startswith("_Z"),
            )
            for exp in macho_meta.exports if exp.name
        ]
        return AbiSnapshot(
            library=path.name, version=version,
            functions=funcs, macho=macho_meta, platform="macho",
        )

    # Text-based: JSON snapshot or Perl dump
    try:
        with open(path, "rb") as f:
            head = f.read(256).decode("utf-8", errors="replace").lstrip()
    except OSError as exc:
        _logger.debug("Failed reading input in _resolve_input: %s", exc, exc_info=True)
        raise AbicheckError("Cannot read input file") from exc

    from .compat.abicc_dump_import import import_abicc_perl_dump, looks_like_perl_dump
    if looks_like_perl_dump(head):
        return import_abicc_perl_dump(path)

    if head.startswith("{"):
        return load_snapshot(path)

    raise AbicheckError(
        "Cannot detect input format. "
        "Expected: ELF (.so), PE (.dll), Mach-O (.dylib), JSON snapshot, or ABICC Perl dump."
    )


def _snapshot_summary(snap: AbiSnapshot) -> dict[str, Any]:
    """Build a compact summary of an ABI snapshot."""
    return {
        "library": snap.library,
        "version": snap.version,
        "platform": snap.platform,
        "functions": len(snap.functions),
        "variables": len(snap.variables),
        "types": len(snap.types),
        "enums": len(snap.enums),
    }


_VALID_FORMATS = frozenset({"json", "sarif", "html", "markdown"})


def _render_output(
    fmt: str,
    result: DiffResult,
    old: AbiSnapshot,
    new: AbiSnapshot,
    *,
    show_only: str | None = None,
    report_mode: str = "full",
    show_impact: bool = False,
    stat: bool = False,
) -> str:
    """Render comparison result in the requested output format."""
    if fmt not in _VALID_FORMATS:
        msg = f"Unknown output format {fmt!r}. Valid formats: {sorted(_VALID_FORMATS)}"
        raise ValueError(msg)
    if stat:
        if fmt == "json":
            from .reporter import to_stat_json
            return to_stat_json(result)
        from .reporter import to_stat
        return to_stat(result)
    if fmt == "json":
        return to_json(result, show_only=show_only, report_mode=report_mode, show_impact=show_impact)
    if fmt == "sarif":
        from .sarif import to_sarif_str
        return to_sarif_str(result, show_only=show_only)
    if fmt == "html":
        from .html_report import generate_html_report
        return generate_html_report(
            result,
            lib_name=old.library,
            old_version=old.version,
            new_version=new.version,
            old_symbol_count=result.old_symbol_count,
            show_only=show_only,
            show_impact=show_impact,
        )
    return to_markdown(result, show_only=show_only, report_mode=report_mode, show_impact=show_impact)



def _impact_category(kind: ChangeKind, policy: str = "strict_abi") -> str:
    """Return the impact category string for a ChangeKind under the given policy.

    When *policy* is not ``strict_abi``, some kinds may be downgraded
    (e.g. ``sdk_vendor`` downgrades source-level renames from ``api_break``
    to ``compatible``).  This ensures per-change impact labels agree with
    the policy-aware verdict.
    """
    breaking, api_break, compatible, risk = policy_kind_sets(policy)
    if kind in breaking:
        return "breaking"
    if kind in api_break:
        return "api_break"
    if kind in risk:
        return "risk"
    if kind in compatible:
        return "compatible"
    _logger.warning("_impact_category: unknown ChangeKind %r, defaulting to breaking", kind)
    return "breaking"  # fail-safe for unknown kinds


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def abi_dump(
    library_path: str,
    headers: list[str] | None = None,
    include_dirs: list[str] | None = None,
    version: str = "unknown",
    language: str = "c++",
    output_path: str | None = None,
) -> str:
    """Dump ABI snapshot of a C/C++ shared library to JSON.

    Extracts the public ABI surface (functions, variables, types, enums)
    from a shared library binary and its public headers.

    Args:
        library_path: Path to .so, .dll, or .dylib file.
        headers: Public header file paths. For ELF (.so), omitting them produces
            a symbol-only snapshot with no type information (strongly recommended
            to supply headers). Not used for PE (.dll) or Mach-O (.dylib) inputs.
        include_dirs: Extra include directories for the C/C++ parser.
        version: Version label to embed in the snapshot (e.g. "1.2.3").
        language: Language mode — "c++" (default) or "c".
        output_path: If provided, write snapshot to this file and return the path.
            Otherwise the snapshot JSON is returned inline.
    """
    t0 = _time.monotonic()
    try:
        lib = _safe_read_path(library_path, label="library_path")
        if not lib.exists():
            return json.dumps({"status": "error", "error": "Library file not found"})

        _check_file_size(lib, label="library_path")
        hdr_paths = [_safe_read_path(h, label="header") for h in (headers or [])]
        inc_paths = [_safe_read_path(d, label="include_dir") for d in (include_dirs or [])]

        # Run the expensive resolve+serialize in a thread with a real timeout
        # so we don't block the MCP stdio server indefinitely.
        with _futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_resolve_input, lib, hdr_paths, inc_paths, version, language)
            try:
                snap = future.result(timeout=MCP_TIMEOUT)
            except _futures.TimeoutError:
                elapsed = _time.monotonic() - t0
                _audit_log("abi_dump", {"library": lib.name}, elapsed, "timeout")
                return json.dumps({"status": "error", "error": f"abi_dump timed out after {MCP_TIMEOUT}s"})
        snap_json = snapshot_to_json(snap)

        elapsed = _time.monotonic() - t0

        if output_path:
            out = _safe_write_path(output_path, label="output_path")
            out.write_text(snap_json, encoding="utf-8")
            _audit_log("abi_dump", {"library": lib.name}, elapsed, "ok")
            return json.dumps({
                "status": "ok",
                "output_path": str(out),
                "summary": _snapshot_summary(snap),
            })

        _audit_log("abi_dump", {"library": lib.name}, elapsed, "ok")
        return json.dumps({
            "status": "ok",
            "summary": _snapshot_summary(snap),
            "snapshot": json.loads(snap_json),
        })
    except Exception as exc:
        elapsed = _time.monotonic() - t0
        _audit_log("abi_dump", {"library": Path(library_path).name}, elapsed, "error")
        _logger.exception("abi_dump failed")
        return json.dumps({"status": "error", "error": _sanitize_error(exc, context="abi_dump")})


@mcp.tool()
def abi_compare(
    old_input: str,
    new_input: str,
    old_headers: list[str] | None = None,
    new_headers: list[str] | None = None,
    headers: list[str] | None = None,
    include_dirs: list[str] | None = None,
    language: str = "c++",
    policy: str = "strict_abi",
    policy_file: str | None = None,
    suppression_file: str | None = None,
    output_format: str = "json",
    show_only: str | None = None,
    report_mode: str = "full",
    show_impact: bool = False,
    stat: bool = False,
) -> str:
    """Compare two ABI surfaces and report breaking changes.

    Each input can be a shared library (.so/.dll/.dylib), a JSON snapshot
    from abi_dump, or an ABICC Perl dump (.pl). The format is auto-detected.

    Returns a structured JSON result with verdict, change summary, and the
    full list of changes. The verdict indicates binary ABI compatibility:
    - NO_CHANGE: identical ABI
    - COMPATIBLE: only additions (backward compatible)
    - COMPATIBLE_WITH_RISK: binary-compatible but deployment risk present
    - API_BREAK: source-level break (recompilation needed)
    - BREAKING: binary ABI break (old binaries will crash)

    Args:
        old_input: Path to old library (.so/.dll/.dylib) or JSON snapshot.
        new_input: Path to new library (.so/.dll/.dylib) or JSON snapshot.
        old_headers: Header files for old side (required if old is ELF binary).
        new_headers: Header files for new side (required if new is ELF binary).
        headers: Header files for both sides (shorthand; overridden by old_headers/new_headers).
        include_dirs: Include directories for the C/C++ parser.
        language: Language mode — "c++" (default) or "c".
        policy: Built-in policy: "strict_abi" (default), "sdk_vendor", or "plugin_abi".
        policy_file: Path to custom YAML policy file (overrides policy parameter).
        suppression_file: Path to YAML suppression file to filter known changes.
        output_format: Output format for the rendered report: "json" (default), "markdown", "sarif", "html".
        show_only: Comma-separated filter tokens (display-only). Severity: breaking, api-break,
            risk, compatible. Element: functions, variables, types, enums, elf. Action: added,
            removed, changed.
        report_mode: "full" (default) or "leaf" (root-type-grouped view).
        show_impact: If True, append an impact summary table.
        stat: If True, emit one-line summary instead of full report.
    """
    t0 = _time.monotonic()
    try:
        old_path = _safe_read_path(old_input, label="old_input")
        new_path = _safe_read_path(new_input, label="new_input")
        for p, label in [(old_path, "old_input"), (new_path, "new_input")]:
            if not p.exists():
                return json.dumps({"status": "error", "error": f"File not found for {label}"})
        _check_file_size(old_path, label="old_input")
        _check_file_size(new_path, label="new_input")

        # Validate policy name only when no policy_file override is provided.
        # policy_file takes precedence over the base policy name.
        if policy_file is None and policy not in VALID_BASE_POLICIES:
            return json.dumps({
                "status": "error",
                "error": f"Unknown policy: {policy!r}. "
                f"Valid policies: {', '.join(sorted(VALID_BASE_POLICIES))}"
            })

        # Resolve per-side headers
        shared = [_safe_read_path(h, label="header") for h in (headers or [])]
        old_h = [_safe_read_path(h, label="old_header") for h in old_headers] if old_headers is not None else shared
        new_h = [_safe_read_path(h, label="new_header") for h in new_headers] if new_headers is not None else shared
        inc = [_safe_read_path(d, label="include_dir") for d in (include_dirs or [])]

        # Validate output_format early (before expensive work)
        if output_format not in _VALID_FORMATS:
            return json.dumps({"status": "error", "error": f"Unknown output format {output_format!r}. Valid: {sorted(_VALID_FORMATS)}"})

        # Validate show_only tokens early
        if show_only:
            from .reporter import ShowOnlyFilter
            try:
                ShowOnlyFilter.parse(show_only)
            except ValueError as exc:
                return json.dumps({"status": "error", "error": f"Invalid show_only: {exc}"})

        # Resolve inputs, load suppression/policy, and compare — all under
        # a real timeout so we don't block the MCP stdio server.
        def _do_compare() -> tuple[AbiSnapshot, AbiSnapshot, DiffResult]:
            old_snap = _resolve_input(old_path, old_h, inc, "old", language)
            new_snap = _resolve_input(new_path, new_h, inc, "new", language)
            suppression = None
            if suppression_file:
                from .suppression import SuppressionList
                suppression = SuppressionList.load(
                    _safe_read_path(suppression_file, label="suppression_file"),
                )
            pf = None
            if policy_file:
                from .policy_file import PolicyFile
                pf = PolicyFile.load(
                    _safe_read_path(policy_file, label="policy_file"),
                )
            return old_snap, new_snap, compare(old_snap, new_snap, suppression=suppression, policy=policy, policy_file=pf)

        with _futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_do_compare)
            try:
                old_snap, new_snap, result = future.result(timeout=MCP_TIMEOUT)
            except _futures.TimeoutError:
                elapsed = _time.monotonic() - t0
                _audit_log("abi_compare", {"old": old_path.name, "new": new_path.name}, elapsed, "timeout")
                return json.dumps({"status": "error", "error": f"abi_compare timed out after {MCP_TIMEOUT}s"})

        # Use the active policy from the result (may differ from input when
        # policy_file overrides the base policy).
        active_policy = result.policy

        # Determine exit code (matches CLI semantics)
        exit_code = 0
        if result.verdict == Verdict.BREAKING:
            exit_code = 4
        elif result.verdict == Verdict.API_BREAK:
            exit_code = 2

        # Build structured response
        response: dict[str, Any] = {
            "status": "ok",
            "verdict": result.verdict.value,
            "exit_code": exit_code,
            "summary": {
                "breaking": len(result.breaking),
                "api_breaks": len(result.source_breaks),
                "risk_changes": len(result.risk),
                "compatible": len(result.compatible),
                "total_changes": len(result.changes),
            },
            "changes": [
                {
                    "kind": c.kind.value,
                    "symbol": c.symbol,
                    "description": c.description,
                    "impact": _impact_category(c.kind, active_policy),
                    "old_value": c.old_value,
                    "new_value": c.new_value,
                    "source_location": c.source_location,
                }
                for c in result.changes
            ],
            "suppressed_count": result.suppressed_count,
        }

        # Include rendered report
        rendered = _render_output(
            output_format, result, old_snap, new_snap,
            show_only=show_only, report_mode=report_mode,
            show_impact=show_impact, stat=stat,
        )
        # When format is json, embed as nested object (not double-encoded string)
        if output_format == "json":
            response["report"] = json.loads(rendered)
        else:
            response["report"] = rendered

        elapsed = _time.monotonic() - t0
        _audit_log(
            "abi_compare",
            {"old": old_path.name, "new": new_path.name},
            elapsed, "ok", verdict=result.verdict.value,
        )
        return json.dumps(response)
    except Exception as exc:
        elapsed = _time.monotonic() - t0
        _audit_log(
            "abi_compare",
            {"old": Path(old_input).name, "new": Path(new_input).name},
            elapsed, "error",
        )
        _logger.exception("abi_compare failed")
        return json.dumps({"status": "error", "error": _sanitize_error(exc, context="abi_compare")})


@mcp.tool()
def abi_list_changes(
    impact: str | None = None,
) -> str:
    """List all ABI change kinds that abicheck can detect.

    Returns an array of change kinds with their impact classification
    and description. Use this to understand what types of ABI breaks
    abicheck detects and how they are classified.

    Args:
        impact: Filter by impact level. One of: "breaking", "api_break",
            "risk", "compatible". If omitted, returns all change kinds.
    """
    filter_set: set[ChangeKind] | None = None
    if impact == "breaking":
        filter_set = BREAKING_KINDS
    elif impact == "api_break":
        filter_set = API_BREAK_KINDS
    elif impact == "risk":
        filter_set = set(RISK_KINDS)
    elif impact == "compatible":
        filter_set = COMPATIBLE_KINDS
    elif impact is not None:
        return json.dumps({
            "status": "error",
            "error": f"Unknown impact filter: {impact!r}. "
            "Use one of: breaking, api_break, risk, compatible"
        })

    results = []
    for kind in sorted(ChangeKind, key=lambda k: k.value):
        if filter_set is not None and kind not in filter_set:
            continue
        entry = policy_for(kind)
        results.append({
            "kind": kind.value,
            "impact": _impact_category(kind),
            "default_verdict": entry.default_verdict.value,
            "description": impact_for(kind),
        })

    return json.dumps({"count": len(results), "change_kinds": results})


@mcp.tool()
def abi_explain_change(
    change_kind: str,
) -> str:
    """Get a detailed explanation of a specific ABI change kind.

    Returns what the change means, why it's dangerous, and what
    impact it has on binary compatibility. Use this after abi_compare
    returns changes to understand and explain each finding.

    Args:
        change_kind: The change kind to explain (e.g. "func_removed",
            "type_size_changed"). Use abi_list_changes to see all available kinds.
    """
    # Look up the ChangeKind enum member
    try:
        kind = ChangeKind(change_kind)
    except ValueError:
        # Try case-insensitive lookup
        for k in ChangeKind:
            if k.value.lower() == change_kind.lower():
                kind = k
                break
        else:
            return json.dumps({
                "status": "error",
                "error": f"Unknown change kind: {change_kind!r}. "
                "Use abi_list_changes to see all available kinds."
            })

    entry = policy_for(kind)
    impact_text = impact_for(kind)
    category = _impact_category(kind)

    result: dict[str, Any] = {
        "kind": kind.value,
        "impact": category,
        "default_verdict": entry.default_verdict.value,
        "severity": entry.severity,
        "description": impact_text,
    }

    # Add fix guidance based on impact category
    if category == "breaking":
        result["fix_guidance"] = (
            "This is a binary ABI break. Old binaries compiled against the previous "
            "version will malfunction (crash, corrupt data, or fail to load). "
            "Options: (1) revert the change, (2) bump the SONAME/major version, "
            "(3) add the old symbol as a compatibility alias."
        )
    elif category == "api_break":
        result["fix_guidance"] = (
            "This is a source-level API break. Existing binaries may still work, "
            "but code compiled against the old headers will fail to build. "
            "Options: (1) revert the change, (2) provide a compatibility typedef/alias, "
            "(3) document the migration path."
        )
    elif category == "risk":
        result["fix_guidance"] = (
            "This change is binary-compatible but introduces deployment risk. "
            "Verify that your target environments satisfy the new requirements "
            "(e.g. minimum glibc version)."
        )
    else:
        result["fix_guidance"] = (
            "This change is backward-compatible. No action required."
        )

    return json.dumps(result)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the abicheck MCP server (stdio transport)."""
    global MCP_TIMEOUT, MCP_MAX_FILE_SIZE, _structured_logging  # noqa: PLW0603

    import argparse
    parser = argparse.ArgumentParser(description="abicheck MCP server")
    parser.add_argument("--timeout", type=int, default=MCP_TIMEOUT,
                        help=f"Timeout in seconds for tool calls (default: {MCP_TIMEOUT})")
    parser.add_argument("--max-file-size", type=int, default=MCP_MAX_FILE_SIZE,
                        help=f"Max input file size in bytes (default: {MCP_MAX_FILE_SIZE})")
    parser.add_argument("--log-format", choices=["text", "json"], default="text",
                        help="Log format: text (default) or json (structured)")
    args = parser.parse_args()

    if args.timeout <= 0:
        parser.error("--timeout must be a positive integer")
    if args.max_file_size <= 0:
        parser.error("--max-file-size must be a positive integer")
    MCP_TIMEOUT = args.timeout
    MCP_MAX_FILE_SIZE = args.max_file_size
    _structured_logging = args.log_format == "json"

    # Redirect logging to stderr to avoid corrupting stdio JSON-RPC
    handler = logging.StreamHandler(sys.stderr)
    if _structured_logging:
        handler.setFormatter(logging.Formatter("%(message)s"))
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s: %(name)s: %(message)s"))
    logger = logging.getLogger("abicheck")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
