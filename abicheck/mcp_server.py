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
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .policy_file import PolicyFile
    from .severity import SeverityConfig
    from .suppression import SuppressionList

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
from .reporter import _finding_id, to_json, to_markdown
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

#: The public depth ladder (ADR-043 D2): exactly the four user-facing rungs.
#: ``full``/``symbols``/``graph`` are internal-only vocabulary and must not
#: leak into the MCP tool surface, matching the public CLI's ``--depth``.
_PUBLIC_DEPTHS = frozenset({"binary", "headers", "build", "source"})


def _validate_public_depth(depth: str | None) -> str | None:
    """Reject any depth spelling outside the public ladder, or ``None``.

    Note this only validates the *spelling* — it does not (yet) enforce that
    the requested depth was actually *reached*. PR #601 (open as of CLAUDE.md
    "M1-6") adds a hard-fail ``DumpDepthNotSatisfiedError`` when an explicit
    ``dump --depth`` isn't satisfied, but that check lives entirely in
    ``cli.py``/``cli_dump_helpers.py`` at the CLI entry point. Neither this
    MCP surface nor ``service.py``'s ``ScanRequest``/``run_scan_subprocess``
    call it — an agent driving abicheck through MCP with an explicit
    ``depth=`` can silently get a result from a shallower evidence tier than
    requested, the same way the CLI itself could before PR #601. Tracked as
    acknowledged remaining work: once PR #601 merges, extend the same
    requested-vs-achieved check to this module and to ``service.py``.
    """
    if depth is not None and depth not in _PUBLIC_DEPTHS:
        raise ValueError(
            f"Unknown depth: {depth!r}. Valid depths: {sorted(_PUBLIC_DEPTHS)}"
        )
    return depth


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
    *,
    public_headers: list[Path] | None = None,
    public_header_dirs: list[Path] | None = None,
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

    ``public_headers`` / ``public_header_dirs``: several MCP tools (``abi_dump``,
    ``abi_compare``, ``abi_audit``) document their plain ``headers`` parameter as
    "Public header files" — they have no separate opt-in provenance flag the way
    ``dump``'s CLI ``-H``/``--public-header`` split does. Callers making that same
    claim should pass the same paths here so declaration provenance is actually
    classified (ADR-024), matching the CLI ``compare --header`` fix.
    """
    from . import service

    return service.resolve_input(
        path,
        headers,
        includes,
        version,
        lang,
        follow_linker_scripts=False,
        public_headers=public_headers,
        public_header_dirs=public_header_dirs,
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
    severity_config: SeverityConfig | None = None,
) -> str:
    """Render comparison result in the requested output format.

    *severity_config*, when given, is forwarded to every format that
    supports it (json, sarif, markdown, stat, html) so an MCP caller passing
    ``severity_*`` arguments to :func:`abi_compare` gets the same
    severity-aware report content the CLI's ``--severity-*`` flags produce —
    without it, the MCP surface had no severity configuration at all.
    """
    if fmt not in _VALID_FORMATS:
        msg = f"Unknown output format {fmt!r}. Valid formats: {sorted(_VALID_FORMATS)}"
        raise ValueError(msg)
    if stat:
        if fmt == "json":
            from .reporter import to_stat_json

            return to_stat_json(result, severity_config=severity_config)
        from .reporter import to_stat

        return to_stat(result, severity_config=severity_config)
    if fmt == "json":
        return to_json(
            result,
            show_only=show_only,
            report_mode=report_mode,
            show_impact=show_impact,
            severity_config=severity_config,
        )
    if fmt == "sarif":
        from .sarif import to_sarif_str

        return to_sarif_str(
            result,
            show_only=show_only,
            report_mode=report_mode,
            severity_config=severity_config,
        )
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
            severity_config=severity_config,
        )
    return to_markdown(
        result,
        show_only=show_only,
        report_mode=report_mode,
        show_impact=show_impact,
        severity_config=severity_config,
    )


def _scoped_verdict_exit_code(verdict: object) -> int:
    """Map a scoped-comparison (--used-by/--required-symbols) Verdict to its
    floor exit code (ADR-043): BREAKING -> 4, API_BREAK -> 2, else 0."""
    value = getattr(verdict, "value", verdict)
    if value == "BREAKING":
        return 4
    if value == "API_BREAK":
        return 2
    return 0


def _scoped_exit_code(
    verdict: object, relevant_changes: list[Any], result: Any,
    severity_config: SeverityConfig | None, policy: str, policy_file: object,
    *, has_missing_contract: bool = False,
) -> int:
    """Scoped-verdict exit code, respecting a severity config when given.

    Mirrors ``cli_compare_helpers._scoped_exit_code``: without this, a
    used_by/required_symbols scope always fell back to the legacy 0/2/4
    verdict floor, silently ignoring any severity_* argument the caller
    passed (parity bug with the severity-aware unscoped path above).

    *has_missing_contract* (a required symbol/version/entrypoint absent from
    the new library) floors the severity-scheme exit code separately from
    *relevant_changes*: a missing contract symbol is BREAKING but is not a
    diff Change, so ``compute_exit_code`` never sees it and would otherwise
    return 0 (Codex review).
    """
    if severity_config is not None:
        from .severity import compute_exit_code, missing_contract_exit_code

        code = compute_exit_code(
            relevant_changes, severity_config,
            policy=policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=policy_file,
        )
        if has_missing_contract:
            code = max(code, missing_contract_exit_code(severity_config))
        return code
    return _scoped_verdict_exit_code(verdict)


def _scoped_severity_summary(
    relevant_changes: list[Any], missing: Iterable[str],
    result: Any, severity_config: SeverityConfig, policy: str, policy_file: object,
) -> tuple[tuple[str, ...], dict[str, int]]:
    """(blocking_categories, per-category counts) for one scoped result.

    Mirrors ``cli_compare_helpers._scoped_severity_summary``: a missing
    contract symbol/version/entrypoint with no matching diff Change is
    folded into ``abi_breaking`` directly here -- into the blocking
    -categories set (when abi_breaking is severity-configured as error,
    matching the exit-code floor) and into the count (always, since a count
    is a factual tally, not a gate decision). A *missing* entry that already
    has a matching Change in *relevant_changes* is excluded via
    ``uncovered_missing_symbols`` so it isn't counted twice.
    """
    from .appcompat import uncovered_missing_symbols
    from .severity import (
        IssueCategory,
        SeverityLevel,
        categorize_changes,
        compute_gate_decision,
    )

    categorized = categorize_changes(
        relevant_changes, policy=policy,
        kind_sets=result._effective_kind_sets(), policy_file=policy_file,
    )
    counts = {
        "abi_breaking": len(categorized.abi_breaking),
        "potential_breaking": len(categorized.potential_breaking),
        "quality_issues": len(categorized.quality_issues),
        "addition": len(categorized.addition),
    }
    gate = compute_gate_decision(
        relevant_changes, severity_config,
        policy=policy, kind_sets=result._effective_kind_sets(), policy_file=policy_file,
    )
    categories = list(gate.blocking_categories)
    uncovered = uncovered_missing_symbols(missing, relevant_changes)
    if uncovered:
        counts["abi_breaking"] += len(uncovered)
        if (
            severity_config.abi_breaking == SeverityLevel.ERROR
            and IssueCategory.ABI_BREAKING.value not in categories
        ):
            categories.append(IssueCategory.ABI_BREAKING.value)
    return tuple(categories), counts


_VERDICT_SEVERITY_RANK = {
    "BREAKING": 3, "API_BREAK": 2, "COMPATIBLE_WITH_RISK": 1,
    "COMPATIBLE": 0, "NO_CHANGE": 0,
}


def _verdict_severity_rank(verdict: object) -> int:
    """Rank a Verdict by severity, independent of any exit-code scheme.

    Mirrors ``cli_compare_helpers._verdict_severity_rank``: under a severity
    scheme a BREAKING app can carry exit code 0 (e.g. an info-only preset),
    so picking the reported scoped verdict by exit code could let a later,
    less-severe app overwrite an earlier BREAKING one merely because their
    exit codes tied at 0 (Codex review).
    """
    value = getattr(verdict, "value", verdict)
    return _VERDICT_SEVERITY_RANK.get(value, 0) if isinstance(value, str) else 0


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
                _resolve_input,
                lib,
                hdr_paths,
                inc_paths,
                version,
                language,
                public_headers=hdr_paths,
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
    severity_preset: str | None = None,
    severity_abi_breaking: str | None = None,
    severity_potential_breaking: str | None = None,
    severity_quality_issues: str | None = None,
    severity_addition: str | None = None,
    used_by: list[str] | None = None,
    required_symbols: list[str] | None = None,
) -> str:
    """Compare two ABI surfaces and report breaking changes.

    Each input can be a shared library (.so/.dll/.dylib), a JSON snapshot
    from abi_dump, or an ABICC Perl dump (.pl). The format is auto-detected.

    Returns a structured JSON result with verdict, change summary, and the
    full list of changes. The verdict indicates binary ABI compatibility:
    - NO_CHANGE: identical ABI
    - COMPATIBLE: no incompatible ABI/API changes (may include additions
      and quality findings; backward compatible)
    - COMPATIBLE_WITH_RISK: binary-compatible but deployment risk present
    - API_BREAK: source-level break (recompilation needed)
    - BREAKING: binary ABI break (old binaries will crash)

    Without any ``severity_*`` argument, ``exit_code`` follows the legacy
    verdict-based scheme (0/2/4, matching the verdicts above). Compatibility
    and "blocks CI" are independent decisions once severity configuration is
    in play (e.g. an addition can be configured to gate CI despite a
    COMPATIBLE verdict) — passing any ``severity_*`` argument switches
    ``exit_code``/``exit_code_scheme`` to the severity-aware scheme (mirrors
    the CLI's ``--severity-*`` flags) and populates a ``severity`` block in a
    JSON ``report``.

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
        severity_preset: Severity preset: "default", "strict", or "info-only".
            Presence of this (or any other severity_* argument) switches
            exit_code to the severity-aware scheme.
        severity_abi_breaking: Override severity for abi_breaking findings
            ("error", "warning", or "info").
        severity_potential_breaking: Override severity for potential_breaking findings.
        severity_quality_issues: Override severity for quality_issues findings.
        severity_addition: Override severity for addition findings.
        used_by: Application binary paths (ADR-043) — scope the comparison to
            what each app actually imports/requires, instead of the full
            library surface. old_input/new_input may be real library binaries
            or JSON snapshots carrying binary evidence (a dump of a real
            library, not headers-only). Mutually exclusive with
            required_symbols. Adds a ``used_by`` list to the response and
            computes ``exit_code`` from the worst-scoped app: the legacy
            BREAKING → 4 / API_BREAK → 2 floor, or — when any ``severity_*``
            argument is given — the severity-aware scheme (which can return
            ``0`` for a scoped BREAKING verdict under ``severity_preset=
            "info-only"``).
        required_symbols: An explicit plugin/host required-entrypoint contract
            (ADR-043) — scope the comparison to only these exported symbols.
            Mutually exclusive with used_by. Adds a ``required_symbol_contract``
            object to the response and computes ``exit_code`` from its verdict
            under the same legacy/severity-aware scheme as ``used_by`` above.
    """
    t0 = _time.monotonic()
    try:
        if used_by and required_symbols:
            return json.dumps(
                {
                    "status": "error",
                    "error": "used_by and required_symbols are mutually exclusive",
                }
            )
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

        # Resolve severity config (any severity_* argument opts into the
        # severity-aware exit-code scheme, matching the CLI's --severity-*
        # flags). Validated early, before the expensive compare work.
        severity_config: SeverityConfig | None = None
        if any(
            value is not None
            for value in (
                severity_preset,
                severity_abi_breaking,
                severity_potential_breaking,
                severity_quality_issues,
                severity_addition,
            )
        ):
            from .errors import PolicyError
            from .severity import resolve_severity_config

            try:
                severity_config = resolve_severity_config(
                    severity_preset,
                    abi_breaking=severity_abi_breaking,
                    potential_breaking=severity_potential_breaking,
                    quality_issues=severity_quality_issues,
                    addition=severity_addition,
                )
            except PolicyError as exc:
                return json.dumps({"status": "error", "error": str(exc)})

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
        def _do_compare() -> tuple[
            AbiSnapshot, AbiSnapshot, DiffResult, PolicyFile | None, SuppressionList | None
        ]:
            old_snap = _resolve_input(
                old_path, old_h, inc, "old", language, public_headers=old_h
            )
            new_snap = _resolve_input(
                new_path, new_h, inc, "new", language, public_headers=new_h
            )
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
                pf,
                suppression,
            )

        with _futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_do_compare)
            try:
                old_snap, new_snap, result, pf, suppression = future.result(timeout=MCP_TIMEOUT)
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

        # Determine exit code: severity-aware scheme when any severity_*
        # argument was given (compatibility and "blocks CI" are independent
        # decisions once severity configuration is in play), else the legacy
        # verdict scheme (matches CLI semantics).
        if severity_config is not None:
            from .severity import compute_exit_code

            exit_code = compute_exit_code(
                result.changes,
                severity_config,
                policy=result.policy,
                kind_sets=result._effective_kind_sets(),
                policy_file=result.policy_file,
            )
        else:
            exit_code = 0
            if result.verdict == Verdict.BREAKING:
                exit_code = 4
            elif result.verdict == Verdict.API_BREAK:
                exit_code = 2

        # Scope the comparison to --used-by apps or a --required-symbols
        # contract (ADR-043): mirrors the CLI's `compare --used-by`/
        # `--required-symbol` folding of the deleted appcompat/plugin-check
        # commands. When given, the scoped verdict's exit code always wins
        # over the severity/legacy scheme above (parity with the CLI).
        exit_code_scheme = "severity" if severity_config is not None else "legacy"
        scoped_key: str | None = None
        scoped_payload: Any = None
        scoped_verdict_value: str | None = None
        if used_by:
            from .appcompat import scope_diff_to_app, uncovered_missing_symbols
            from .service import detect_binary_format

            old_lib: Any = old_path if detect_binary_format(old_path) is not None else old_snap
            new_lib: Any = new_path if detect_binary_format(new_path) is not None else new_snap
            for lib, p, label in (
                (old_lib, old_path, "old_input"), (new_lib, new_path, "new_input"),
            ):
                has_binary_evidence = isinstance(lib, Path) or any(
                    getattr(lib, field, None) is not None for field in ("elf", "pe", "macho")
                )
                if not has_binary_evidence:
                    return json.dumps(
                        {
                            "status": "error",
                            "error": f"used_by requires old_input/new_input to be "
                            f"real library binaries, or JSON snapshots carrying "
                            f"binary evidence (a dump of a real library, not "
                            f"headers-only); {label} ({p}) is neither.",
                        }
                    )
            summaries = []
            worst_exit = 0
            worst_verdict = None
            worst_verdict_rank = -1
            # Keyed by the change's semantic identity (kind/symbol/old/new/
            # location/description, via `_finding_id`) -- not id(change) --
            # so a Change or missing symbol shared by two tied apps (e.g.
            # both import the same removed symbol) collapses to one entry
            # instead of being tallied once per app (Codex review) --
            # `_scoped_severity_summary` runs once at the end over this
            # deduplicated union, not per app summed together. `id()` alone
            # under-deduplicates PE_ORDINAL_RETARGETED findings: each app's
            # `scope_diff_to_app` call synthesizes a fresh `Change` object
            # for the same underlying ordinal retarget (see
            # `cli_compare_helpers._apply_used_by_scoping`).
            worst_changes: dict[str, Any] = {}
            worst_missing: set[str] = set()
            # Union across ALL apps of which findings this --used-by gate
            # cares about -- SARIF/JUnit consult this to make their own
            # result levels/failure counts follow the scoped gate (CLI-audit
            # P1), mirroring cli_compare_helpers._apply_used_by_scoping.
            relevant_finding_ids: set[str] = set()
            # Union across ALL apps of relevant Change objects (not just
            # ids) so scoped-only changes (e.g. PE_ORDINAL_RETARGETED,
            # synthesized fresh per app and never added to result.changes)
            # can still be rendered by SARIF/JUnit (Codex review, mirrors
            # cli_compare_helpers._apply_used_by_scoping).
            relevant_changes_by_id: dict[str, Any] = {}
            missing_labels: set[str] = set()
            for app in used_by:
                app_path = _safe_read_path(app, label="used_by")
                if not app_path.exists():
                    return json.dumps(
                        {"status": "error", "error": f"used_by app not found: {app}"}
                    )
                _check_file_size(app_path, label="used_by")
                scoped = scope_diff_to_app(
                    result, app_path, old_lib, new_lib,
                    policy=active_policy, policy_file=pf, suppression=suppression,
                )
                relevant_finding_ids.update(_finding_id(c) for c in scoped.breaking_for_app)
                relevant_changes_by_id.update(
                    {_finding_id(c): c for c in scoped.breaking_for_app}
                )
                # A missing symbol/version already covered by a relevant
                # Change (e.g. FUNC_REMOVED) must not also become a
                # synthetic missing-contract finding (Codex review, mirrors
                # cli_compare_helpers._apply_used_by_scoping's dedup).
                missing_labels.update(
                    uncovered_missing_symbols(
                        list(scoped.missing_symbols) + list(scoped.missing_versions),
                        scoped.breaking_for_app,
                    )
                )
                summaries.append(
                    {
                        "app": scoped.app_path,
                        "verdict": scoped.verdict.value,
                        "required_symbol_count": scoped.required_symbol_count,
                        "missing_symbols": scoped.missing_symbols,
                        "missing_versions": scoped.missing_versions,
                        "relevant_change_count": len(scoped.breaking_for_app),
                        "symbol_coverage": round(scoped.symbol_coverage, 1),
                    }
                )
                app_exit = _scoped_exit_code(
                    scoped.verdict, scoped.breaking_for_app, result,
                    severity_config, active_policy, pf,
                    has_missing_contract=bool(
                        scoped.missing_symbols or scoped.missing_versions
                    ),
                )
                # exit code (gating) and verdict (reporting) are maxed/ranked
                # independently -- see _verdict_severity_rank.
                if severity_config is not None:
                    if app_exit > worst_exit:
                        worst_changes = {_finding_id(c): c for c in scoped.breaking_for_app}
                        worst_missing = set(scoped.missing_symbols) | set(scoped.missing_versions)
                    elif app_exit == worst_exit:
                        worst_changes.update({_finding_id(c): c for c in scoped.breaking_for_app})
                        worst_missing |= set(scoped.missing_symbols) | set(scoped.missing_versions)
                worst_exit = max(worst_exit, app_exit)
                rank = _verdict_severity_rank(scoped.verdict)
                if worst_verdict is None or rank >= worst_verdict_rank:
                    worst_verdict_rank = rank
                    worst_verdict = scoped.verdict
            scoped_key = "used_by"
            scoped_payload = summaries
            exit_code = worst_exit
            exit_code_scheme = "scoped"
            scoped_verdict_value = worst_verdict.value if worst_verdict is not None else None
            # Mirror the CLI's result attributes (cli_compare_helpers._apply_used_by_scoping)
            # so _fold_scoped_compat_into_text below can fold the same scoping into the
            # rendered report, not just this response's top-level fields.
            result.used_by = summaries  # type: ignore[attr-defined]
            result.scoped_verdict = worst_verdict  # type: ignore[attr-defined]
            result.scoped_exit_code = worst_exit  # type: ignore[attr-defined]
            scoped_scheme = "severity" if severity_config is not None else "legacy"
            result.scoped_exit_code_scheme = scoped_scheme  # type: ignore[attr-defined]
            result.gate_scope = "used_by"  # type: ignore[attr-defined]
            result.scoped_relevant_finding_ids = frozenset(relevant_finding_ids)  # type: ignore[attr-defined]
            result.scoped_missing_labels = tuple(sorted(missing_labels))  # type: ignore[attr-defined]
            _existing_ids = {_finding_id(c) for c in result.changes}
            result.scoped_only_changes = tuple(  # type: ignore[attr-defined]
                c for fid, c in relevant_changes_by_id.items() if fid not in _existing_ids
            )
            if severity_config is not None:
                categories, counts = _scoped_severity_summary(
                    list(worst_changes.values()), worst_missing,
                    result, severity_config, active_policy, pf,
                )
                result.scoped_blocking_categories = categories  # type: ignore[attr-defined]
                result.scoped_severity_counts = counts  # type: ignore[attr-defined]
        elif required_symbols:
            from .appcompat import (
                scope_diff_to_required_symbols,
                uncovered_missing_symbols,
            )

            scoped_host = scope_diff_to_required_symbols(
                result, old_snap, new_snap, required_symbols,
                policy=active_policy, policy_file=pf,
            )
            scoped_key = "required_symbol_contract"
            scoped_payload = {
                "verdict": scoped_host.verdict.value,
                "required_entrypoints": sorted(scoped_host.required_entrypoints),
                "missing_entrypoints": scoped_host.missing_entrypoints,
                "relevant_change_count": len(scoped_host.breaking_for_host),
                "coverage": round(scoped_host.coverage, 1),
            }
            exit_code = _scoped_exit_code(
                scoped_host.verdict, scoped_host.breaking_for_host, result,
                severity_config, active_policy, pf,
                has_missing_contract=bool(scoped_host.missing_entrypoints),
            )
            exit_code_scheme = "scoped"
            scoped_verdict_value = scoped_host.verdict.value
            result.required_symbols = scoped_payload  # type: ignore[attr-defined]
            result.scoped_verdict = scoped_host.verdict  # type: ignore[attr-defined]
            result.scoped_exit_code = exit_code  # type: ignore[attr-defined]
            scoped_scheme = "severity" if severity_config is not None else "legacy"
            result.scoped_exit_code_scheme = scoped_scheme  # type: ignore[attr-defined]
            result.gate_scope = "required_symbol"  # type: ignore[attr-defined]
            result.scoped_relevant_finding_ids = frozenset(  # type: ignore[attr-defined]
                _finding_id(c) for c in scoped_host.breaking_for_host
            )
            # An entrypoint already covered by a relevant Change must not
            # also become a synthetic missing-contract finding (Codex
            # review).
            result.scoped_missing_labels = tuple(sorted(  # type: ignore[attr-defined]
                uncovered_missing_symbols(
                    scoped_host.missing_entrypoints, scoped_host.breaking_for_host
                )
            ))
            # Scoped-only changes: relevant to the host contract but never
            # added to result.changes (mirrors the used_by branch above).
            _existing_ids = {_finding_id(c) for c in result.changes}
            result.scoped_only_changes = tuple(  # type: ignore[attr-defined]
                c for c in scoped_host.breaking_for_host if _finding_id(c) not in _existing_ids
            )
            if severity_config is not None:
                categories, counts = _scoped_severity_summary(
                    scoped_host.breaking_for_host, scoped_host.missing_entrypoints,
                    result, severity_config, active_policy, pf,
                )
                result.scoped_blocking_categories = categories  # type: ignore[attr-defined]
                result.scoped_severity_counts = counts  # type: ignore[attr-defined]

        # Build structured response. When a used_by/required_symbols scope is in
        # effect, mirror the CLI JSON contract (`_fold_scoped_compat_into_text`):
        # the scoped verdict becomes the primary `verdict` the exit code reflects,
        # with the full-library verdict kept as `full_verdict` for context (Codex
        # review — a caller that only reads `verdict` must not see the full
        # library's BREAKING alongside a scoped-compatible exit_code: 0).
        response: dict[str, Any] = {
            "status": "ok",
            "verdict": scoped_verdict_value if scoped_verdict_value is not None else result.verdict.value,
            "full_verdict": result.verdict.value,
            "exit_code": exit_code,
            "exit_code_scheme": exit_code_scheme,
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
        if scoped_key is not None:
            response[scoped_key] = scoped_payload
            # Scoped-only changes/missing-contract labels are relevant to the
            # scoped gate but never land in result.changes -- without folding
            # them into the top-level "changes" array too, a --used-by/
            # --required-symbol response whose only gated issue is one of
            # these shows an empty "changes" list despite a scoped
            # verdict/exit_code that blocks, leaving a caller that only reads
            # this array with nothing to explain the failure (Codex review,
            # mirrors the identical fold-in in
            # cli_compare_helpers._fold_scoped_compat_into_text).
            existing_ids = {_finding_id(c) for c in result.changes}
            for c in getattr(result, "scoped_only_changes", ()) or ():
                if _finding_id(c) not in existing_ids:
                    response["changes"].append(
                        {
                            "kind": c.kind.value,
                            "symbol": c.symbol,
                            "description": c.description,
                            "impact": _impact_category(c.kind, active_policy),
                            "old_value": c.old_value,
                            "new_value": c.new_value,
                            "source_location": c.source_location,
                        }
                    )
            from .severity import missing_contract_exit_code

            missing_kind = (
                "used_by_missing_symbol"
                if getattr(result, "gate_scope", None) == "used_by"
                else "required_symbol_missing"
            )
            blocks = (
                severity_config is None
                or missing_contract_exit_code(severity_config) != 0
            )
            for label in getattr(result, "scoped_missing_labels", ()) or ():
                response["changes"].append(
                    {
                        "kind": missing_kind,
                        "symbol": label,
                        "description": (
                            f"Required symbol/version '{label}' is missing "
                            "from the new library."
                        ),
                        # A missing-contract label has no ChangeKind to run
                        # through _impact_category, but its severity is known
                        # (blocks_gate) -- reuse the same "breaking"/
                        # "compatible" label the CLI text report and severity
                        # gate already use for this, so the summary tally
                        # below has something to count it under.
                        "impact": "breaking" if blocks else "compatible",
                        "old_value": None,
                        "new_value": None,
                        "source_location": None,
                        "relevant_to_gate": True,
                        "blocks_gate": blocks,
                    }
                )

            # Recompute summary now that scoped-only changes/missing-contract
            # labels were folded into "changes" above -- otherwise a run
            # gated purely by one of these (e.g. a missing required symbol,
            # or a scoped-only PE_ORDINAL_RETARGETED change) reports
            # total_changes: 0 alongside a BREAKING verdict/nonzero exit_code,
            # since the per-category counts were computed from result.changes
            # before the fold-in (CodeRabbit review).
            impact_tally = {"breaking": 0, "api_break": 0, "risk": 0, "compatible": 0}
            for c in response["changes"]:
                impact_tally[c["impact"]] = impact_tally.get(c["impact"], 0) + 1
            response["summary"] = {
                "breaking": impact_tally["breaking"],
                "api_breaks": impact_tally["api_break"],
                "risk_changes": impact_tally["risk"],
                "compatible": impact_tally["compatible"],
                "total_changes": len(response["changes"]),
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
            severity_config=severity_config,
        )
        # When format is json, embed as nested object (not double-encoded string)
        if scoped_key is not None:
            from .cli_compare_helpers import _fold_scoped_compat_into_text

            # Fold the same scoped-verdict swap/sections into the *rendered*
            # report as the top-level fields above, mirroring the CLI's
            # --secondary-format behavior — otherwise a client reading
            # response["report"] sees the unscoped full-library verdict even
            # though the top-level verdict/exit_code are scoped (Codex review).
            rendered = _fold_scoped_compat_into_text(
                rendered, output_format, result, severity_config=severity_config,
                show_only=show_only,
            )
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
                _resolve_input,
                lib,
                hdr_paths,
                inc_paths,
                "",
                language,
                public_headers=hdr_paths,
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
    depth: str | None = None,
    changed_paths: list[str] | None = None,
) -> str:
    """Dry-run scan cost estimate for a project (ADR-035 D10 / ADR-043).

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
        depth: Coarse evidence-depth selector: "binary", "headers", "build", or
            "source" (None = inferred from inputs, escalating with the
            changed-path risk score once seeded).
        changed_paths: Changed-path set for the focused (D7) replay-scope estimate.
    """
    t0 = _time.monotonic()
    try:
        from .service import Budget, ScanRequest, estimate_scan

        bin_path = _safe_read_path(binary_path, label="binary_path")
        if not bin_path.exists():
            return json.dumps({"status": "error", "error": "Binary file not found"})
        try:
            depth = _validate_public_depth(depth)
        except ValueError as exc:
            return json.dumps({"status": "error", "error": str(exc)})
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
            mode="pr",
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
    against: str | None = None,
    depth: str | None = None,
    changed_paths: list[str] | None = None,
    language: str = "c++",
) -> str:
    """Run a deterministic source-intelligence scan (ADR-035 D3/D10 / ADR-043).

    The typed engine behind the ``scan`` CLI: classify → always-on tier
    (compiler-free pattern pre-scan + intra-version cross-source checks) → the
    pinned evidence level (inferred from inputs, or pinned via ``depth``),
    POI-focused — and, when ``against`` is given, a ``compare`` against it.
    Returns one coverage-/confidence-annotated :class:`ScanResult`. Authority
    rule preserved: source/cross-source findings are RISK/API_BREAK only,
    never BREAKING on their own.

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
        against: Previous build's dump/library to compare against (omit for a
            single-release audit — the always-on hygiene catalog runs either way).
        depth: Coarse evidence-depth selector: "binary", "headers", "build", or
            "source" (None = inferred from inputs, escalating with the
            changed-path risk score once seeded).
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
        try:
            depth = _validate_public_depth(depth)
        except ValueError as exc:
            return json.dumps({"status": "error", "error": str(exc)})
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
        base_path = _safe_read_path(against, label="against") if against else None
        if base_path is not None:
            _check_file_size(base_path, label="against")

        req = ScanRequest(
            binaries=[bin_path],
            headers=hdr_paths,
            includes=inc_paths,
            public_header_dirs=phd_paths,
            sources=src_path,
            compile_db=cdb_path,
            baseline=base_path,
            # Absence of --against is a one-build audit; presence is compare-too
            # (ADR-043) — neither is a separate mode argument on the MCP surface.
            mode="audit" if base_path is None else "pr",
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
