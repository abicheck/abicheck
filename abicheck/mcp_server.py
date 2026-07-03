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

from .checker import DiffResult
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
from .model import AbiSnapshot
from .reporter import to_json, to_markdown
from .serialization import snapshot_to_json
from .service import compare_snapshots

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
        raise ValueError(f"{label} must have a .json extension, got: {p.suffix!r}")

    # Block writes to sensitive system locations.
    # Use resolved Path objects to handle symlinks (/etc -> /private/etc on macOS)
    # and canonicalize traversal sequences (../../etc bypasses raw-string checks).
    _os = platform.system()
    if _os in ("Linux", "Darwin"):
        sensitive_system_dirs = [
            Path("/etc"),
            Path("/bin"),
            Path("/sbin"),
            Path("/usr/bin"),
            Path("/usr/sbin"),
            Path("/boot"),
            Path("/sys"),
            Path("/proc"),
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
            raise ValueError(f"{label} points to a sensitive system path")

    # Block writes to SSH/credential directories.
    # Resolve both sides to handle symlinks (e.g. ~/.ssh → /private/home/user/.ssh).
    home = Path.home().resolve()
    for sensitive_dir in [
        (home / ".ssh").resolve(),
        (home / ".aws").resolve(),
        (home / ".gnupg").resolve(),
    ]:
        try:
            p.relative_to(sensitive_dir)
            raise ValueError(f"{label} points to a sensitive credential directory")
        except ValueError as e:
            if "credential" in str(e):
                raise

    return p


def _sanitize_error(exc: Exception, *, context: str = "operation") -> str:
    """Return a safe error message that does not leak filesystem paths or internals."""
    # Known domain errors: safe to surface as-is
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

    Thin wrapper over :func:`abicheck.service.resolve_input` — the single source
    of truth for format detection, raw BTF/CTF blobs, and native (ELF/PE/Mach-O)
    dumping. The MCP surface is framework-free, so the service's
    ``SnapshotError`` / ``ValidationError`` (both ``AbicheckError`` subclasses)
    propagate unchanged for the tool handlers to convert into structured error
    payloads.

    ``follow_linker_scripts=False``: the MCP tools enforce ``MCP_MAX_FILE_SIZE``
    via :func:`_check_file_size` on the *caller-supplied* path before resolving.
    Following a GNU ld linker script would parse an ``INPUT()``/``GROUP()``
    target that never went through that guard, so a tiny script pointing at a
    huge library could defeat the resource limit. Disabling the follow keeps the
    size check authoritative (and matches the MCP server's pre-unification
    behaviour, which never followed linker scripts).
    """
    from . import service

    return service.resolve_input(
        path, headers, includes, version, lang, follow_linker_scripts=False
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
        return to_json(
            result,
            show_only=show_only,
            report_mode=report_mode,
            show_impact=show_impact,
        )
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
    return to_markdown(
        result, show_only=show_only, report_mode=report_mode, show_impact=show_impact
    )


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
    _logger.warning(
        "_impact_category: unknown ChangeKind %r, defaulting to breaking", kind
    )
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
        inc_paths = [
            _safe_read_path(d, label="include_dir") for d in (include_dirs or [])
        ]

        # Run the expensive resolve+serialize in a thread with a real timeout
        # so we don't block the MCP stdio server indefinitely.
        with _futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                _resolve_input, lib, hdr_paths, inc_paths, version, language
            )
            try:
                snap = future.result(timeout=MCP_TIMEOUT)
            except _futures.TimeoutError:
                elapsed = _time.monotonic() - t0
                _audit_log("abi_dump", {"library": lib.name}, elapsed, "timeout")
                return json.dumps(
                    {
                        "status": "error",
                        "error": f"abi_dump timed out after {MCP_TIMEOUT}s",
                    }
                )
        snap_json = snapshot_to_json(snap)

        elapsed = _time.monotonic() - t0

        if output_path:
            out = _safe_write_path(output_path, label="output_path")
            out.write_text(snap_json, encoding="utf-8")
            _audit_log("abi_dump", {"library": lib.name}, elapsed, "ok")
            return json.dumps(
                {
                    "status": "ok",
                    "output_path": str(out),
                    "summary": _snapshot_summary(snap),
                }
            )

        _audit_log("abi_dump", {"library": lib.name}, elapsed, "ok")
        return json.dumps(
            {
                "status": "ok",
                "summary": _snapshot_summary(snap),
                "snapshot": json.loads(snap_json),
            }
        )
    except Exception as exc:
        elapsed = _time.monotonic() - t0
        _audit_log("abi_dump", {"library": Path(library_path).name}, elapsed, "error")
        _logger.exception("abi_dump failed")
        return json.dumps(
            {"status": "error", "error": _sanitize_error(exc, context="abi_dump")}
        )


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
                return json.dumps(
                    {"status": "error", "error": f"File not found for {label}"}
                )
        _check_file_size(old_path, label="old_input")
        _check_file_size(new_path, label="new_input")

        # Validate policy name only when no policy_file override is provided.
        # policy_file takes precedence over the base policy name.
        if policy_file is None and policy not in VALID_BASE_POLICIES:
            return json.dumps(
                {
                    "status": "error",
                    "error": f"Unknown policy: {policy!r}. "
                    f"Valid policies: {', '.join(sorted(VALID_BASE_POLICIES))}",
                }
            )

        # Resolve per-side headers
        shared = [_safe_read_path(h, label="header") for h in (headers or [])]
        old_h = (
            [_safe_read_path(h, label="old_header") for h in old_headers]
            if old_headers is not None
            else shared
        )
        new_h = (
            [_safe_read_path(h, label="new_header") for h in new_headers]
            if new_headers is not None
            else shared
        )
        inc = [_safe_read_path(d, label="include_dir") for d in (include_dirs or [])]

        # Validate output_format early (before expensive work)
        if output_format not in _VALID_FORMATS:
            return json.dumps(
                {
                    "status": "error",
                    "error": f"Unknown output format {output_format!r}. Valid: {sorted(_VALID_FORMATS)}",
                }
            )

        # Validate show_only tokens early
        if show_only:
            from .reporter import ShowOnlyFilter

            try:
                ShowOnlyFilter.parse(show_only)
            except ValueError as exc:
                return json.dumps(
                    {"status": "error", "error": f"Invalid show_only: {exc}"}
                )

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
            return (
                old_snap,
                new_snap,
                compare_snapshots(
                    old_snap,
                    new_snap,
                    suppression=suppression,
                    policy=policy,
                    policy_file=pf,
                ),
            )

        with _futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_do_compare)
            try:
                old_snap, new_snap, result = future.result(timeout=MCP_TIMEOUT)
            except _futures.TimeoutError:
                elapsed = _time.monotonic() - t0
                _audit_log(
                    "abi_compare",
                    {"old": old_path.name, "new": new_path.name},
                    elapsed,
                    "timeout",
                )
                return json.dumps(
                    {
                        "status": "error",
                        "error": f"abi_compare timed out after {MCP_TIMEOUT}s",
                    }
                )

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
            output_format,
            result,
            old_snap,
            new_snap,
            show_only=show_only,
            report_mode=report_mode,
            show_impact=show_impact,
            stat=stat,
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
            elapsed,
            "ok",
            verdict=result.verdict.value,
        )
        return json.dumps(response)
    except Exception as exc:
        elapsed = _time.monotonic() - t0
        _audit_log(
            "abi_compare",
            {"old": Path(old_input).name, "new": Path(new_input).name},
            elapsed,
            "error",
        )
        _logger.exception("abi_compare failed")
        return json.dumps(
            {"status": "error", "error": _sanitize_error(exc, context="abi_compare")}
        )


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
        return json.dumps(
            {
                "status": "error",
                "error": f"Unknown impact filter: {impact!r}. "
                "Use one of: breaking, api_break, risk, compatible",
            }
        )

    results = []
    for kind in sorted(ChangeKind, key=lambda k: k.value):
        if filter_set is not None and kind not in filter_set:
            continue
        entry = policy_for(kind)
        results.append(
            {
                "kind": kind.value,
                "impact": _impact_category(kind),
                "default_verdict": entry.default_verdict.value,
                "description": impact_for(kind),
            }
        )

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
            return json.dumps(
                {
                    "status": "error",
                    "error": f"Unknown change kind: {change_kind!r}. "
                    "Use abi_list_changes to see all available kinds.",
                }
            )

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


@mcp.tool()
def abi_audit(
    library_path: str,
    headers: list[str] | None = None,
    include_dirs: list[str] | None = None,
    language: str = "c++",
) -> str:
    """Single-release ABI-hygiene audit — no baseline (ADR-035 D8).

    Runs the intra-version cross-source validation engine plus the compiler-free
    lexical pattern pre-scan over ONE build and returns a "bad ABI hygiene"
    catalog: accidental ABI surface (exported_not_public), public-not-exported
    declarations, header/build-context mismatch, and private-header leaks, plus
    advisory pattern facts. These findings are never BREAKING on their own
    (authority rule) — they default to RISK/API_BREAK and are advisory.

    Args:
        library_path: Path to .so/.dll/.dylib or a JSON snapshot.
        headers: Public header files (classifies declarations + drives the
            pattern pre-scan). Strongly recommended — most checks skip cleanly
            without public-header provenance.
        include_dirs: Extra include directories for the C/C++ parser.
        language: Language mode — "c++" (default) or "c".
    """
    t0 = _time.monotonic()
    try:
        from .buildsource.crosscheck import run_crosschecks
        from .buildsource.pattern_scan import scan_files
        from .checker_policy import API_BREAK_KINDS

        lib = _safe_read_path(library_path, label="library_path")
        if not lib.exists():
            return json.dumps({"status": "error", "error": "Library file not found"})
        _check_file_size(lib, label="library_path")
        hdr_paths = [_safe_read_path(h, label="header") for h in (headers or [])]
        inc_paths = [
            _safe_read_path(d, label="include_dir") for d in (include_dirs or [])
        ]

        with _futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                _resolve_input, lib, hdr_paths, inc_paths, "", language
            )
            try:
                snap = future.result(timeout=MCP_TIMEOUT)
            except _futures.TimeoutError:
                elapsed = _time.monotonic() - t0
                _audit_log("abi_audit", {"library": lib.name}, elapsed, "timeout")
                return json.dumps(
                    {
                        "status": "error",
                        "error": f"abi_audit timed out after {MCP_TIMEOUT}s",
                    }
                )

        cc = run_crosschecks(snap)
        pattern = scan_files([*hdr_paths], None)
        has_api_break = any(c.kind in API_BREAK_KINDS for c in cc.findings)
        exit_code = 2 if has_api_break else 0
        elapsed = _time.monotonic() - t0
        _audit_log("abi_audit", {"library": lib.name}, elapsed, "ok")
        return json.dumps(
            {
                "status": "ok",
                "verdict": "API_BREAK" if has_api_break else "COMPATIBLE",
                "exit_code": exit_code,
                "catalog": cc.to_dict(),
                "pattern_scan": pattern.to_dict(),
            }
        )
    except Exception as exc:
        elapsed = _time.monotonic() - t0
        _audit_log("abi_audit", {"library": Path(library_path).name}, elapsed, "error")
        _logger.exception("abi_audit failed")
        return json.dumps(
            {"status": "error", "error": _sanitize_error(exc, context="abi_audit")}
        )


@mcp.tool()
def abi_estimate(
    binary_path: str,
    headers: list[str] | None = None,
    include_dirs: list[str] | None = None,
    sources: str | None = None,
    compile_db: str | None = None,
    mode: str = "pr",
    source_method: str | None = None,
    depth: str | None = None,
    changed_paths: list[str] | None = None,
) -> str:
    """Dry-run scan cost estimate for a project (ADR-035 D10).

    Probes the project (TU count from the compile DB or source tree, public-header
    fan-out) and returns the projected per-layer cost of the chosen level WITHOUT
    running any compiler or parsing any binary — so a maintainer/agent can pick a
    depth/budget on measured cost. Scans nothing.

    Args:
        binary_path: Library/artifact the scan would target (existence checked).
        headers: Public header files (for the L2 header-AST fan-out estimate).
        include_dirs: Extra include directories.
        sources: Source tree (compile DB auto-discovered within it).
        compile_db: Explicit compile_commands.json (else discovered in sources).
        mode: Fixed (L,S) preset — "pr" (default), "pr-deep", "baseline", "audit".
        source_method: Precise S-axis level (s0..s6 or auto); None = mode preset.
        depth: Coarse L-axis selector (binary|headers|build|source|full).
        changed_paths: Changed-path set for the focused (D7) replay-scope estimate.
    """
    t0 = _time.monotonic()
    try:
        from .service import Budget, ScanRequest, estimate_scan

        bin_path = _safe_read_path(binary_path, label="binary_path")
        if not bin_path.exists():
            return json.dumps({"status": "error", "error": "Binary file not found"})
        hdr_paths = [_safe_read_path(h, label="header") for h in (headers or [])]
        inc_paths = [
            _safe_read_path(d, label="include_dir") for d in (include_dirs or [])
        ]
        src_path = _safe_read_path(sources, label="sources") if sources else None
        cdb_path = (
            _safe_read_path(compile_db, label="compile_db") if compile_db else None
        )

        req = ScanRequest(
            binaries=[bin_path],
            headers=hdr_paths,
            includes=inc_paths,
            sources=src_path,
            compile_db=cdb_path,
            mode=mode,
            source_method=source_method,
            depth=depth,
            changed_paths=list(changed_paths or []),
            # Distinguish an *explicit* empty diff ([], a seeded no-op PR → s0
            # floor) from an omitted arg (None, unseeded → mode preset), matching
            # the CLI's seeded handling (Codex review).
            seeded=changed_paths is not None,
            budget=Budget(),
        )
        estimates = estimate_scan(req)
        total = sum(e.est_seconds for e in estimates)
        elapsed = _time.monotonic() - t0
        _audit_log("abi_estimate", {"binary": bin_path.name}, elapsed, "ok")
        return json.dumps(
            {
                "status": "ok",
                "mode": mode,
                "estimate": [e.to_dict() for e in estimates],
                "total_est_seconds": round(total, 3),
            }
        )
    except Exception as exc:
        elapsed = _time.monotonic() - t0
        _audit_log("abi_estimate", {"binary": Path(binary_path).name}, elapsed, "error")
        _logger.exception("abi_estimate failed")
        return json.dumps(
            {"status": "error", "error": _sanitize_error(exc, context="abi_estimate")}
        )


@mcp.tool()
def abi_scan(
    binary_path: str,
    headers: list[str] | None = None,
    include_dirs: list[str] | None = None,
    public_header_dirs: list[str] | None = None,
    sources: str | None = None,
    compile_db: str | None = None,
    baseline: str | None = None,
    mode: str = "pr",
    source_method: str | None = None,
    depth: str | None = None,
    changed_paths: list[str] | None = None,
    language: str = "c++",
) -> str:
    """Run a deterministic source-intelligence scan (ADR-035 D3/D10).

    The typed engine behind the ``scan`` CLI: classify → always-on tier
    (compiler-free pattern pre-scan + intra-version cross-source checks) → the
    pinned evidence level (the ``mode`` preset or an explicit
    ``source_method``/``depth``), POI-focused — and, when ``baseline`` is given,
    a ``compare`` against it. Returns one coverage-/confidence-annotated
    :class:`ScanResult`. Authority rule preserved: source/cross-source findings
    are RISK/API_BREAK only, never BREAKING on their own.

    Args:
        binary_path: Library/artifact (or JSON snapshot) to scan.
        headers: Public header files (provenance + pattern pre-scan).
        include_dirs: Extra include directories for the parser.
        public_header_dirs: Directories whose headers are public; establishes the
            public/internal boundary so the leakage / RTTI / exported-vs-public
            cross-checks run instead of skipping. A directory passed via ``headers``
            also counts; a lone umbrella header file cannot establish a boundary.
        sources: Source tree (compile DB auto-discovered within it).
        compile_db: Explicit compile_commands.json (else discovered in sources).
        baseline: Previous build's dump/library to compare against (omit for a
            single-release run; use ``mode="audit"`` for the hygiene catalog).
        mode: Fixed (L,S) preset — "pr" (default), "pr-deep", "baseline", "audit".
        source_method: Precise S-axis level (s0..s6 or auto); None = mode preset.
        depth: Coarse L-axis selector (binary|headers|build|source|full).
        changed_paths: Changed-path set focusing the scan (ADR-035 D7).
        language: Language mode — "c++" (default) or "c".
    """
    t0 = _time.monotonic()
    try:
        from .service import Budget, ScanRequest, run_scan_subprocess

        bin_path = _safe_read_path(binary_path, label="binary_path")
        if not bin_path.exists():
            return json.dumps({"status": "error", "error": "Binary file not found"})
        _check_file_size(bin_path, label="binary_path")
        hdr_paths = [_safe_read_path(h, label="header") for h in (headers or [])]
        inc_paths = [
            _safe_read_path(d, label="include_dir") for d in (include_dirs or [])
        ]
        phd_paths: list[Path] = []
        for d in public_header_dirs or []:
            p = _safe_read_path(d, label="public_header_dir")
            # Match the CLI option (exists=True, file_okay=False): a public-header
            # boundary must be a real directory, else apply_provenance would tag
            # every declaration INTERNAL against a path that matches nothing,
            # producing misleading origin classification (CodeRabbit).
            if not p.is_dir():
                return json.dumps(
                    {
                        "status": "error",
                        "error": f"public_header_dir must be an existing directory: {d}",
                    }
                )
            phd_paths.append(p)
        src_path = _safe_read_path(sources, label="sources") if sources else None
        cdb_path = (
            _safe_read_path(compile_db, label="compile_db") if compile_db else None
        )
        if cdb_path is not None:
            _check_file_size(cdb_path, label="compile_db")
        base_path = _safe_read_path(baseline, label="baseline") if baseline else None
        if base_path is not None:
            _check_file_size(base_path, label="baseline")

        req = ScanRequest(
            binaries=[bin_path],
            headers=hdr_paths,
            includes=inc_paths,
            public_header_dirs=phd_paths,
            sources=src_path,
            compile_db=cdb_path,
            baseline=base_path,
            mode=mode,
            source_method=source_method,
            depth=depth,
            changed_paths=list(changed_paths or []),
            seeded=changed_paths is not None,
            budget=Budget(),
            lang=language,
        )

        # Run in a killable child process so a deep/hung scan that exceeds the
        # timeout is *terminated* (process + clang subtree) instead of orphaned to
        # keep burning CPU after the timeout response is sent (Codex review).
        try:
            payload = run_scan_subprocess(req, MCP_TIMEOUT)
        except TimeoutError:
            elapsed = _time.monotonic() - t0
            _audit_log("abi_scan", {"binary": bin_path.name}, elapsed, "timeout")
            return json.dumps(
                {
                    "status": "error",
                    "error": f"abi_scan timed out after {MCP_TIMEOUT}s",
                }
            )

        elapsed = _time.monotonic() - t0
        _audit_log("abi_scan", {"binary": bin_path.name}, elapsed, "ok")
        return json.dumps({"status": "ok", **payload})
    except Exception as exc:
        elapsed = _time.monotonic() - t0
        _audit_log("abi_scan", {"binary": Path(binary_path).name}, elapsed, "error")
        _logger.exception("abi_scan failed")
        return json.dumps(
            {"status": "error", "error": _sanitize_error(exc, context="abi_scan")}
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the abicheck MCP server (stdio transport)."""
    global MCP_TIMEOUT, MCP_MAX_FILE_SIZE, _structured_logging  # noqa: PLW0603

    import argparse

    parser = argparse.ArgumentParser(description="abicheck MCP server")
    parser.add_argument(
        "--timeout",
        type=int,
        default=MCP_TIMEOUT,
        help=f"Timeout in seconds for tool calls (default: {MCP_TIMEOUT})",
    )
    parser.add_argument(
        "--max-file-size",
        type=int,
        default=MCP_MAX_FILE_SIZE,
        help=f"Max input file size in bytes (default: {MCP_MAX_FILE_SIZE})",
    )
    parser.add_argument(
        "--log-format",
        choices=["text", "json"],
        default="text",
        help="Log format: text (default) or json (structured)",
    )
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
