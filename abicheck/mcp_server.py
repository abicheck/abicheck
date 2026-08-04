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
import sys
import time as _time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .severity import SeverityConfig

from . import mcp_shared
from .api_types import CompareRequest, CompareResult, DumpRequest, InputSpec
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
)
from .compile_context import CompileContext
from .contract_scoped_promotion import stamp_scoped_result_findings
from .errors import ProfileMismatchError, ScopeMismatchError
from .mcp_server_inputs import (  # noqa: F401  (re-exported for API stability)
    _ALLOWED_BINARY_SUFFIXES,
    _ALLOWED_OUTPUT_SUFFIXES,
    _PUBLIC_DEPTHS,
    _compile_context_from_args,
    _contract_mode_error,
    _detect_binary_format,
    _existing_path,
    _public_header_dir_paths,
    _resolve_input,
    _safe_write_path,
    _source_abi_only_frontend_error,
    _validate_public_depth,
)
from .mcp_server_verdicts import (  # noqa: F401  (re-exported for API stability)
    _VERDICT_SEVERITY_RANK,
    _impact_category,
    _mcp_change_entry,
    _scoped_exit_code,
    _scoped_severity_summary,
    _scoped_verdict_exit_code,
    _verdict_severity_rank,
)
from .mcp_shared import (
    _audit_log,
    _call_with_timeout,
    _check_file_size,
    _logger,
    _safe_read_path,
    _sanitize_error,
    mcp,
)
from .model import AbiSnapshot
from .reporter import _finding_id, to_json, to_markdown
from .serialization import snapshot_to_json

# ---------------------------------------------------------------------------
# Configuration (environment variables or CLI flags)
# ---------------------------------------------------------------------------
#
# MCP_TIMEOUT/MCP_MAX_FILE_SIZE/_structured_logging live in mcp_shared (the
# single source of truth every tool module reads, ADR-021b D2/D3/D4) — this
# module accesses them as `mcp_shared.NAME`, never as a bare imported name,
# so main()'s runtime mutation is visible everywhere; see mcp_shared's own
# module docstring for why a bare `from .mcp_shared import MCP_TIMEOUT`
# would silently go stale.

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
    contract_evaluation: bool = False,
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
        contract_evaluation=contract_evaluation,
    )


@mcp.tool()
def abi_dump(
    library_path: str,
    headers: list[str] | None = None,
    include_dirs: list[str] | None = None,
    public_header_dirs: list[str] | None = None,
    version: str = "unknown",
    language: str = "c++",
    depth: str | None = None,
    sources: str | None = None,
    build_info: str | None = None,
    dump_manifest: str | None = None,
    include_dependencies: bool = True,
    dwarf_only: bool = False,
    debug_format: str | None = None,
    ast_frontend: str = "auto",
    gcc_path: str | None = None,
    gcc_prefix: str | None = None,
    gcc_options: str | None = None,
    sysroot: str | None = None,
    nostdinc: bool = False,
    frontend_context: str = "host",
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
            Mutually exclusive with dump_manifest.
        include_dirs: Extra include directories for the C/C++ parser.
        public_header_dirs: Directories whose headers are public, for declaration
            provenance classification — beyond any directory already passed via
            ``headers``.
        version: Version label to embed in the snapshot (e.g. "1.2.3").
        language: Language mode — "c++" (default) or "c".
        depth: Evidence depth to collect — "binary" (symbols only), "headers"
            (+header AST), "build" (+build context), or "source" (+source
            replay). Omitted, the depth is inferred from the inputs
            (``sources`` implies source, ``build_info`` implies build). An
            explicit value is a *floor*: if the resolved snapshot does not
            reach it, this returns an error instead of a weaker snapshot,
            matching ``abicheck dump --depth``.
        sources: Source tree (or prebuilt evidence pack) supplying inline L3-L5
            build/source evidence — mirrors ``dump --sources``.
        build_info: Out-of-tree build dir / compile_commands.json / pack
            supplying build context — mirrors ``dump --build-info``.
        dump_manifest: Path to a ``--dump-manifest`` YAML document describing
            multiple translation units to compile and merge into one snapshot,
            instead of a single ``headers`` list. Mutually exclusive with
            ``headers``. ELF only.
        include_dependencies: Keep declarations whose defining header is a
            toolchain/system header (default True, matching the Python API's own
            default — ``dump --include-dependencies``'s opt-out is the CLI
            spelling of the same switch, whose default is the opposite way
            round). Set False for a dependency-scoped snapshot.
        dwarf_only: Use DWARF debug info as the primary data source even when
            headers are available.
        debug_format: Force the ELF debug format — "auto", "dwarf", "btf", or
            "ctf". ELF only: a forced format against a PE/Mach-O input is a
            validation error rather than a silently ignored request.
        ast_frontend: AST frontend — "auto" (default), "castxml", "clang" or
            "hybrid" drive L2 header parsing. "android" is source-ABI-replay
            only: it has no header-AST path, so header parsing falls back to
            "auto" rather than failing, and it is meaningful only alongside
            source inputs.
        gcc_path: Explicit compiler binary for the header frontend.
        gcc_prefix: Cross-toolchain prefix for the header frontend.
        gcc_options: Extra compiler flags for the header frontend, as one
            shell-quoted string.
        sysroot: Alternate sysroot for the header frontend.
        nostdinc: Suppress the standard include paths for the header frontend.
        frontend_context: Which AST context the header frontend targets —
            "host" (default) or "device" (SYCL/DPC++ offload target).
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
        for hdr in hdr_paths:
            _check_file_size(hdr, label="header")
        inc_paths = [
            _safe_read_path(d, label="include_dir") for d in (include_dirs or [])
        ]
        try:
            phd_paths = _public_header_dir_paths(public_header_dirs)
        except ValueError as exc:
            return json.dumps({"status": "error", "error": str(exc)})
        try:
            depth = _validate_public_depth(depth)
        except ValueError as exc:
            return json.dumps({"status": "error", "error": str(exc)})
        # Existence is checked, not just containment: `sources`/`build_info`
        # infer a collect mode from being set at all, and only an *explicit*
        # depth arms the floor -- so a typo would otherwise collect nothing
        # and still answer "ok" (Codex review, P1). The CLI's own
        # `click.Path(exists=True)` rejects the same input.
        try:
            src_path = _existing_path(sources, label="sources") if sources else None
            bi_path = (
                _existing_path(build_info, label="build_info") if build_info else None
            )
            manifest_path = (
                _existing_path(dump_manifest, label="dump_manifest")
                if dump_manifest
                else None
            )
            compile_ctx = _compile_context_from_args(
                ast_frontend=ast_frontend,
                gcc_path=gcc_path,
                gcc_prefix=gcc_prefix,
                gcc_options=gcc_options,
                sysroot=sysroot,
                nostdinc=nostdinc,
                frontend_context=frontend_context,
            )
        except ValueError as exc:
            return json.dumps({"status": "error", "error": str(exc)})
        manifest = None
        if manifest_path is not None:
            from .dump_manifest import load_manifest

            manifest = load_manifest(manifest_path)

        request = DumpRequest(
            input=InputSpec(
                path=lib,
                headers=tuple(hdr_paths),
                includes=tuple(inc_paths),
                version=version,
                public_header_dirs=tuple(phd_paths),
                include_dependencies=include_dependencies,
                sources=src_path,
                build_info=bi_path,
                dump_manifest=manifest,
                compile=compile_ctx,
                # Same guard as `abi_compare`: this tool size-checks only the
                # caller-supplied path, so following a GNU ld script's INPUT()
                # target would reach a file that never went through it.
                follow_linker_scripts=False,
            ),
            lang=language,
            frontend=ast_frontend,
            depth=depth,
            dwarf_only=dwarf_only,
            debug_format=debug_format,
            frontend_context=frontend_context,
        )

        # Run the expensive resolve+serialize in a thread with a real timeout
        # so we don't block the MCP stdio server indefinitely. Uses the
        # shared _call_with_timeout (mcp_shared.py) rather than a local
        # `with ThreadPoolExecutor(...) as pool:` block -- the `with` form's
        # `shutdown(wait=True)` on exit blocked even after a TimeoutError had
        # already fired, defeating the timeout for a genuinely stuck resolve
        # (Codex review; same fix already applied to the four project tools
        # in mcp_server_project.py).
        #
        # G33 Phase 5: one typed request through the Tier-2 chokepoint, the
        # way `abi_compare` builds a `CompareRequest`. This tool used to call
        # `_resolve_input` with a fixed five-argument subset, which is why it
        # could not express a depth, inline build/source evidence, a dump
        # manifest, or a compile context at all.
        def _resolve() -> AbiSnapshot:
            from .service import run_dump_request

            return run_dump_request(request)

        try:
            snap = _call_with_timeout(_resolve)
        except _futures.TimeoutError:
            elapsed = _time.monotonic() - t0
            _audit_log("abi_dump", {"library": lib.name}, elapsed, "timeout")
            return json.dumps(
                {
                    "status": "error",
                    "error": f"abi_dump timed out after {mcp_shared.MCP_TIMEOUT}s",
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
    diagnostic_comparison: bool = False,
    contract_evaluation: bool = False,
    contract_mode: str | None = None,
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
        diagnostic_comparison: ADR-050 D2's sanctioned escape hatch. By default,
            a genuine ``ExtractionContract`` mismatch between old_input and
            new_input (old/new were not extracted under a comparable
            profile/scope) makes this tool return
            ``{"status": "not_comparable", "reason": ...}`` instead of a
            verdict. Setting this True downgrades that into a tentative diff
            whose report is stamped ``assurance: "none"``, so the caller can
            still see *a* result but knows not to trust it fully. Not needed,
            and does nothing, on a comparable pair.
        contract_evaluation: ADR-049's contract-relevance evaluator.
            **Authoritative since Phase 7** — relevance is classified before
            compatibility policy, and policy scores only the findings it
            evaluates, so enabling this can change ``verdict``,
            ``compatibility_decision`` and ``exit_code``. When True, each
            finding in the response's
            ``changes``/``out_of_surface_changes`` lists gains a
            ``contract_relevance`` (``IN_CONTRACT``/``PROVEN_OUT_OF_CONTRACT``/
            ``UNKNOWN_UNPROVEN``/``UNKNOWN_UNRESOLVED``/``NOT_APPLICABLE``),
            ``contract_reason_code``, and — when resolved — ``contract_assurance``
            field, reflecting whether the finding falls inside the library's
            declared public contract. Each finding also gains
            ``contract_evidence_refs`` (which evidence records its decision
            rests on), and — for ``output_format="json"`` only, since every
            other format returns a rendered report *string* — the response's
            ``report`` gains a ``contract_context`` block: the observed
            provider evidence, the resolved evaluation context, and the
            decision receipt, so a decision can be replayed or re-evaluated
            later without re-reading the binaries. A finding this evaluator
            does not evaluate carries ``compatibility_evaluation_status:
            "NOT_EVALUATED"``, a ``null`` ``compatibility_decision`` and
            ``gate_contribution: 0`` — it stays fully reported and keeps its
            ``ChangeKind``, it just did not gate. **A second, orthogonal
            contract-coverage axis** (ADR-049 Phase 7): when the selected
            contract domain cannot be closed on
            the available evidence, that contributes ``1`` to ``exit_code``,
            so an otherwise compatible comparison can return ``exit_code: 1``.
            The contribution never lowers a real break's ``2``/``4``. The
            response's top-level ``contract_coverage`` block states what was
            found and what it contributed, whatever ``output_format`` is
            selected. Accepting incomplete coverage requires
            ``contract.unresolved: warn``, which only a ``kind: contract``
            pack can state and which **this tool does not expose** — it is
            available on the ``compare`` and ``scan`` CLIs via ``--pack``.
            Default False; the response is unchanged from today's shape
            unless this is set.
        contract_mode: ADR-049 Phase 6's ``--contract``: which evidence domain
            ``contract_evaluation`` judges against — "public" (the
            header-derived declared surface), "exports" (the binary's own
            export table plus its type closure), or "all" (no root/closure
            evidence required). Omitted, the domain follows this tool's
            public-surface scoping. Requires ``contract_evaluation``. Since
            ADR-049 Phase 7 the domain decides which findings compatibility
            policy scores, so selecting one can change ``verdict`` and
            ``exit_code``, and it is what the orthogonal contract-coverage
            axis is answered against.
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
        for hdr in {*old_h, *new_h}:
            _check_file_size(hdr, label="header")
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

        # `CompareRequest.validate()` states these same two rules, but reaching
        # them means building the request first; checked here so a usage error
        # reads like this tool's other ones instead of a sanitized exception,
        # and so `abi_scan` (whose `ScanRequest` has no `validate()`) can share
        # exactly one implementation of them.
        contract_error = _contract_mode_error(contract_mode, contract_evaluation)
        if contract_error is not None:
            return json.dumps({"status": "error", "error": contract_error})

        # ADR-055 D4: one typed request through the Tier-2 chokepoint —
        # `run_compare_request` resolves both sides, loads policy and
        # suppression, and classifies. This tool used to do all three itself
        # (its own `_resolve_input` pair, its own `PolicyFile.load`/
        # `SuppressionList.load`, and `compare_snapshots` for the middle step
        # only), which made it a second compare engine that happened to share
        # one internal function with the first.
        #
        # The MCP-specific resource guards stay here, ahead of the request:
        # `_safe_read_path` (containment) and `_check_file_size` validate the
        # caller-supplied policy/suppression paths *before* anything loads
        # them, and `InputSpec.follow_linker_scripts=False` keeps
        # MCP_MAX_FILE_SIZE authoritative for the library paths themselves
        # (see `_resolve_input`'s docstring for why following a linker script
        # would defeat it). Those are guards on untrusted input, not
        # comparison logic — unlike the resolve/load/classify work above,
        # they have no business in the shared service layer.
        suppression_path = None
        if suppression_file:
            suppression_path = _safe_read_path(
                suppression_file, label="suppression_file"
            )
            _check_file_size(suppression_path, label="suppression_file")
        policy_path = None
        if policy_file:
            policy_path = _safe_read_path(policy_file, label="policy_file")
            _check_file_size(policy_path, label="policy_file")

        def _do_compare() -> CompareResult:
            from .service import run_compare_request

            request = CompareRequest(
                old=InputSpec(
                    path=old_path,
                    headers=tuple(old_h),
                    includes=tuple(inc),
                    version="old",
                    follow_linker_scripts=False,
                ),
                new=InputSpec(
                    path=new_path,
                    headers=tuple(new_h),
                    includes=tuple(inc),
                    version="new",
                    follow_linker_scripts=False,
                ),
                lang=language,
                policy=policy,
                policy_file_path=policy_path,
                suppress=suppression_path,
                diagnostic_comparison=diagnostic_comparison,
                contract_evaluation=contract_evaluation,
                contract_mode=contract_mode,
            )
            return run_compare_request(request)

        try:
            compare_result = _call_with_timeout(_do_compare)
            old_snap = compare_result.old_snapshot
            new_snap = compare_result.new_snapshot
            result = compare_result.diff
            # The resolved policy/suppression the run actually classified with
            # — read back off the result rather than re-derived here, so the
            # `used_by`/`required_symbols` scoping below cannot apply a
            # different policy than the comparison it is scoping.
            pf = result.policy_file
            suppression = compare_result.suppression
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
                    "error": f"abi_compare timed out after {mcp_shared.MCP_TIMEOUT}s",
                }
            )
        except (ProfileMismatchError, ScopeMismatchError) as exc:
            # ADR-050 D2: a genuine comparability-gate mismatch is a
            # distinct, expected outcome -- not an "error" the caller
            # should treat as an abicheck bug -- so it gets its own
            # status, ordered before the generic except Exception below.
            elapsed = _time.monotonic() - t0
            _audit_log(
                "abi_compare",
                {"old": old_path.name, "new": new_path.name},
                elapsed,
                "not_comparable",
            )
            return json.dumps({"status": "not_comparable", "reason": str(exc)})

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

        # A finding relevant to used_by/required_symbols scoping that is
        # ALSO already in result.changes (the ordinary case, e.g. a plain
        # FUNC_REMOVED) never appears in scoped_only_changes -- that
        # collection is deliberately built excluding anything already in
        # result.changes (see the `not in _existing_ids` filters above) --
        # so without this, only a freshly-synthesized scoped-only finding
        # ever got the explicit-evidence override below; a reused one stayed
        # under whatever (possibly weaker, e.g. unknown_unresolved on a
        # binary-only snapshot) decision _apply_contract_evaluation_shadow
        # already computed for it from header surfaces alone (Codex review,
        # fresh evidence). Must run before the "changes" list comprehension
        # below, which serializes result.changes as-is.
        if contract_evaluation:
            stamp_scoped_result_findings(result, finding_id=_finding_id)
            # ADR-049 Phase 5: this front end resolves one
            # `CompatibilityEvaluationConfig` from its own real arguments and
            # installs it, the same seam `cli_compare_receipt` is for the
            # native CLI -- rather than patching the one field it happened to
            # know about onto the core verb's argument-shaped reconstruction.
            from .mcp_compare_receipt import record_resolved_config

            record_resolved_config(
                result,
                exit_code_scheme=exit_code_scheme,
                severity_config=severity_config,
                policy=policy,
                policy_file=pf,
                policy_file_path=policy_file,
                suppression=suppression,
                suppression_path=suppression_file,
                # ADR-049 Phase 6/7: the selected domain decides which findings
                # policy scores, so the receipt must name the one that produced
                # this verdict rather than the built-in default (Codex review).
                contract_mode=contract_mode,
                severity={
                    "preset": severity_preset,
                    "abi_breaking": severity_abi_breaking,
                    "potential_breaking": severity_potential_breaking,
                    "quality_issues": severity_quality_issues,
                    "addition": severity_addition,
                },
            )

        # Build structured response. When a used_by/required_symbols scope is in
        # effect, mirror the CLI JSON contract (`_fold_scoped_compat_into_text`):
        # the scoped verdict becomes the primary `verdict` the exit code reflects,
        # with the full-library verdict kept as `full_verdict` for context (Codex
        # review — a caller that only reads `verdict` must not see the full
        # library's BREAKING alongside a scoped-compatible exit_code: 0).
        # ADR-049 §7: the coverage axis is orthogonal and gates every front
        # end, not only the CLI ones. Without this the tool could return
        # `exit_code: 0` beside a report stating the coverage contribution
        # was 1, so a client gating on the top-level code accepts a run its
        # own report says was gated (Codex review).
        # The block goes beside the code it explains, for *every* format: only
        # the JSON report carries the ledger, so a markdown/sarif/html request
        # otherwise got a change-free report next to `exit_code: 1` with no
        # diagnostic anywhere (Codex review). The CLI's own rule -- stay quiet
        # when the rendered report already says it -- does not transfer: there
        # the notice is a second copy on stderr beside a piped document, here
        # it is a sibling key a client would have to branch on `output_format`
        # to find.
        from .contract_coverage_exit import coverage_response_block, fold_coverage_exit

        coverage = coverage_response_block(result, base_exit=exit_code)
        exit_code = fold_coverage_exit(exit_code, result)

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
                _mcp_change_entry(
                    c, active_policy,
                    severity_config=severity_config,
                    kind_sets=result._effective_kind_sets(),
                    policy_file=result.policy_file,
                )
                for c in result.changes
            ],
            "suppressed_count": result.suppressed_count,
        }
        # ADR-049 D1: `gate_contribution` is the number that *actually*
        # gated, and under used_by/required_symbols the scoped gate is what
        # this response's `exit_code` reports -- so a full-library number on
        # a finding the selected consumer never uses is stale here for the
        # same reason it is in the CLI's JSON fold. This response embedded a
        # correctly-zeroed CLI report (`_fold_scoped_compat_into_text` below)
        # beside a top-level `changes` array still carrying `4` on a run that
        # exited 0 -- one document disagreeing with itself (Codex review,
        # reproduced). Shared helper, so the two front ends cannot drift.
        # Runs before the scoped-only/missing-contract fold-in below: those
        # are the scoped gate's own findings and must keep their numbers.
        if scoped_key is not None:
            from .contract_gating import zero_scoped_out_gate_contributions

            zero_scoped_out_gate_contributions(response, result)
        if coverage is not None:
            response["contract_coverage"] = coverage
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
            # scoped_only entries are already stamped by
            # stamp_scoped_result_findings above when contract_evaluation is
            # set -- no per-item stamp call needed here anymore.
            existing_ids = {_finding_id(c) for c in result.changes}
            scoped_only = getattr(result, "scoped_only_changes", ()) or ()
            for c in scoped_only:
                if _finding_id(c) not in existing_ids:
                    response["changes"].append(
                        _mcp_change_entry(
                            c, active_policy,
                            severity_config=severity_config,
                            kind_sets=result._effective_kind_sets(),
                            policy_file=result.policy_file,
                        )
                    )
            from .finding_identity import missing_contract_kind
            from .severity import missing_contract_exit_code

            missing_kind = missing_contract_kind(getattr(result, "gate_scope", None))
            blocks = (
                severity_config is None
                or missing_contract_exit_code(severity_config) != 0
            )
            for label in getattr(result, "scoped_missing_labels", ()) or ():
                from .finding_identity import missing_contract_finding

                identity = missing_contract_finding(missing_kind, label)
                missing_entry: dict[str, object] = {
                    "kind": missing_kind,
                    "symbol": label,
                    "description": identity.description,
                    # Same join key the CLI JSON fold emits, for the same
                    # reason: the decision stamped below is keyed by it in
                    # ADR-049's `decision_receipt` (Codex review).
                    "finding_id": _finding_id(identity),
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
                if contract_evaluation:
                    # ADR-049 D1's full shape -- see the identical call in
                    # `cli_compare_fold`: this entry is often the only
                    # blocking finding the response carries.
                    from .contract_scoped_promotion import (
                        missing_contract_gate_contribution,
                        stamp_missing_contract_entry,
                    )

                    stamp_missing_contract_entry(
                        missing_entry,
                        gate_contribution=missing_contract_gate_contribution(
                            severity_config, blocks
                        ),
                    )
                response["changes"].append(missing_entry)

            # Recompute summary now that scoped-only changes/missing-contract
            # labels were folded into "changes" above -- otherwise a run
            # gated purely by one of these (e.g. a missing required symbol,
            # or a scoped-only PE_ORDINAL_RETARGETED change) reports
            # total_changes: 0 alongside a BREAKING verdict/nonzero exit_code,
            # since the per-category counts were computed from result.changes
            # before the fold-in (CodeRabbit review).
            # ADR-049 D1: `impact` is derived from the ChangeKind alone, so a
            # NOT_EVALUATED finding still carries "breaking" -- tallying it
            # here would put `summary.breaking: 1` beside a verdict policy
            # never scored it into, the same contradiction the four
            # DiffResult buckets are already filtered to avoid
            # (`checker_types._evaluated_changes`). `total_changes` stays
            # inclusive: the finding is reported, it just did not gate.
            # Not reachable through today's `abi_compare`, whose arguments
            # carry no contract-domain selector (see `mcp_compare_receipt`),
            # so every finding surviving public-surface scoping resolves
            # IN_CONTRACT -- the guard is what keeps that true by
            # construction rather than by that argument (CodeRabbit review).
            impact_tally = {"breaking": 0, "api_break": 0, "risk": 0, "compatible": 0}
            for c in response["changes"]:
                if c.get("compatibility_evaluation_status") == "NOT_EVALUATED":
                    continue
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
            contract_evaluation=contract_evaluation,
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
                show_only=show_only, report_mode=report_mode,
                contract_evaluation=contract_evaluation,
            )
        if output_format == "json":
            response["report"] = json.loads(rendered)
            if contract_evaluation and scoped_key is not None:
                # _fold_scoped_compat_into_text (cli_compare_fold.py, shared
                # with the CLI, which has no contract_evaluation concept at
                # all) reuses the same already-stamped result.scoped_only_changes
                # Change objects for its own scoped-only entries -- those
                # pick up the stamp for free via _change_to_dict's own
                # _add_contract_evaluation_fields call. Its missing-contract
                # labels, though, are freshly-built plain dicts independent
                # of the top-level response["changes"] missing_entry above,
                # so the two documented findings arrays disagreed: the
                # top-level array carried contract fields on a missing-label
                # entry, the embedded report's own copy of that same finding
                # did not (Codex review, fresh evidence).
                # --report-mode root-cause additionally serializes the exact
                # same missing-label finding a second time, into
                # root_causes[].findings (_add_entries_to_root_causes,
                # cli_compare_fold.py) -- the *same* dict object as the one
                # in "changes" before serialization, but a JSON round trip
                # (json.dumps then json.loads above) always produces
                # independent dict copies, so stamping "changes" alone
                # leaves the root-cause copy of this same finding unstamped
                # (Codex review, fresh evidence).
                from .contract_scoped_promotion import (
                    missing_contract_gate_contribution,
                    stamp_missing_contract_entry,
                )

                candidate_lists = [response["report"].get("changes")]
                for group in response["report"].get("root_causes") or ():
                    if isinstance(group, dict):
                        candidate_lists.append(group.get("findings"))
                for entries in candidate_lists:
                    if not isinstance(entries, list):
                        continue
                    for entry in entries:
                        if (
                            isinstance(entry, dict)
                            and entry.get("kind") == missing_kind
                        ):
                            # The *same* helper (and the same resolved
                            # contribution) the top-level entry above uses,
                            # rather than the relevance-only stamp: the fold
                            # already applies it to its own copies today, so
                            # this re-stamp is idempotent -- but stamping a
                            # strict subset of the fields here is how the two
                            # arrays would silently diverge again the moment
                            # it stops (CodeRabbit review).
                            stamp_missing_contract_entry(
                                entry,
                                gate_contribution=missing_contract_gate_contribution(
                                    severity_config, blocks
                                ),
                            )
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
        for hdr in hdr_paths:
            _check_file_size(hdr, label="header")
        inc_paths = [
            _safe_read_path(d, label="include_dir") for d in (include_dirs or [])
        ]

        def _resolve() -> AbiSnapshot:
            return _resolve_input(
                lib,
                hdr_paths,
                inc_paths,
                "",
                language,
                public_headers=hdr_paths,
            )

        try:
            snap = _call_with_timeout(_resolve)
        except _futures.TimeoutError:
            elapsed = _time.monotonic() - t0
            _audit_log("abi_audit", {"library": lib.name}, elapsed, "timeout")
            return json.dumps(
                {
                    "status": "error",
                    "error": f"abi_audit timed out after {mcp_shared.MCP_TIMEOUT}s",
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
    build_info: str | None = None,
    against: str | None = None,
    depth: str | None = None,
    changed_paths: list[str] | None = None,
    language: str = "c++",
    policy: str = "strict_abi",
    policy_file: str | None = None,
    suppression_file: str | None = None,
    contract_evaluation: bool = False,
    contract_mode: str | None = None,
    ast_frontend: str = "auto",
    gcc_path: str | None = None,
    gcc_prefix: str | None = None,
    gcc_options: str | None = None,
    sysroot: str | None = None,
    nostdinc: bool = False,
    frontend_context: str = "host",
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
        build_info: Out-of-tree build dir / compile_commands.json / pack
            supplying build context — mirrors ``scan --build-info``.
        against: Previous build's dump/library to compare against (omit for a
            single-release audit — the always-on hygiene catalog runs either way).
        depth: Coarse evidence-depth selector: "binary", "headers", "build", or
            "source" (None = inferred from inputs, escalating with the
            changed-path risk score once seeded).
        changed_paths: Changed-path set focusing the scan (ADR-035 D7).
        language: Language mode — "c++" (default) or "c".
        policy: With ``against``: built-in policy — "strict_abi" (default),
            "sdk_vendor", or "plugin_abi". Ignored for a one-build audit, which
            renders no comparison verdict (ADR-049 Phase 5 §6.4 config-surface
            parity with ``abi_compare``).
        policy_file: With ``against``: custom YAML policy file (overrides
            ``policy``).
        suppression_file: With ``against``: YAML suppression file filtering
            known changes out of the comparison.
        contract_evaluation: With ``against``: stamp each comparison finding
            with its ADR-049 contract decision, exactly as ``abi_compare``'s own
            argument does. Authoritative since ADR-049 Phase 7 — it changes the
            verdict and the exit code, and an incomplete contract domain
            contributes the orthogonal coverage exit.
        contract_mode: With ``contract_evaluation``: which evidence domain the
            decision is judged against — "public", "exports", or "all". Omitted,
            the domain follows the scan's public-surface scoping.
        ast_frontend: L2 header-AST frontend — "auto" (default), "castxml",
            "clang", or "hybrid". Unlike ``abi_dump``, "android" is rejected
            here: it is source-ABI-replay only, and a scan has no
            request-level frontend to carry it into source replay, so it would
            be accepted and then silently ignored.
        gcc_path: Explicit compiler binary for the header frontend.
        gcc_prefix: Cross-toolchain prefix for the header frontend.
        gcc_options: Extra compiler flags for the header frontend, as one
            shell-quoted string.
        sysroot: Alternate sysroot for the header frontend.
        nostdinc: Suppress the standard include paths for the header frontend.
        frontend_context: Which AST context the header frontend targets —
            "host" (default) or "device" (SYCL/DPC++ offload target).
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
        for hdr in hdr_paths:
            _check_file_size(hdr, label="header")
        inc_paths = [
            _safe_read_path(d, label="include_dir") for d in (include_dirs or [])
        ]
        try:
            phd_paths = _public_header_dir_paths(public_header_dirs)
        except ValueError as exc:
            return json.dumps({"status": "error", "error": str(exc)})
        # Same existence rule as `abi_dump` (Codex review, P1): every one of
        # these infers evidence collection from being set, and the `scan` CLI
        # declares all four `click.Path(exists=True)`. `sources`/`compile_db`
        # predate this PR and carried the identical hole -- fixed alongside
        # `build_info` rather than left as a knowingly-broken twin.
        try:
            src_path = _existing_path(sources, label="sources") if sources else None
            cdb_path = (
                _existing_path(compile_db, label="compile_db") if compile_db else None
            )
            bi_path = (
                _existing_path(build_info, label="build_info") if build_info else None
            )
            base_path = _existing_path(against, label="against") if against else None
            compile_ctx = _compile_context_from_args(
                ast_frontend=ast_frontend,
                gcc_path=gcc_path,
                gcc_prefix=gcc_prefix,
                gcc_options=gcc_options,
                sysroot=sysroot,
                nostdinc=nostdinc,
                frontend_context=frontend_context,
            )
        except ValueError as exc:
            return json.dumps({"status": "error", "error": str(exc)})

        # ADR-049 Phase 5 §6.4: a `--against` comparison is scoped/suppressed/
        # policy-classified the same way a direct `compare` is, so this tool
        # exposes the same three inputs `abi_compare` does. Same MCP-local
        # guards, applied before anything loads them (G33 Phase 5).
        if policy_file is None and policy not in VALID_BASE_POLICIES:
            return json.dumps(
                {
                    "status": "error",
                    "error": f"Unknown policy: {policy!r}. "
                    f"Valid policies: {', '.join(sorted(VALID_BASE_POLICIES))}",
                }
            )
        suppression_path = None
        if suppression_file:
            suppression_path = _safe_read_path(
                suppression_file, label="suppression_file"
            )
            _check_file_size(suppression_path, label="suppression_file")
        policy_path = None
        if policy_file:
            policy_path = _safe_read_path(policy_file, label="policy_file")
            _check_file_size(policy_path, label="policy_file")
        # `CompareRequest.validate()` states the not-found case for
        # `abi_compare`; `ScanRequest` has no `validate()`, so without this a
        # missing file reaches the loader and surfaces as a sanitized
        # FileNotFoundError instead of naming the argument at fault.
        for missing_path, label in (
            (suppression_path, "suppression_file"),
            (policy_path, "policy_file"),
        ):
            if missing_path is not None and not missing_path.exists():
                return json.dumps(
                    {"status": "error", "error": f"{label} not found: {missing_path}"}
                )
        contract_error = _contract_mode_error(contract_mode, contract_evaluation)
        if contract_error is not None:
            return json.dumps({"status": "error", "error": contract_error})
        frontend_error = _source_abi_only_frontend_error(ast_frontend)
        if frontend_error is not None:
            return json.dumps({"status": "error", "error": frontend_error})
        from .service import load_suppression_and_policy

        suppression, loaded_policy_file = load_suppression_and_policy(
            suppression_path, policy, policy_path
        )

        req = ScanRequest(
            binaries=[bin_path],
            headers=hdr_paths,
            includes=inc_paths,
            public_header_dirs=phd_paths,
            sources=src_path,
            compile_db=cdb_path,
            build_info=bi_path,
            baseline=base_path,
            # Absence of --against is a one-build audit; presence is compare-too
            # (ADR-043) — neither is a separate mode argument on the MCP surface.
            mode="audit" if base_path is None else "pr",
            depth=depth,
            changed_paths=list(changed_paths or []),
            seeded=changed_paths is not None,
            budget=Budget(),
            lang=language,
            compile=compile_ctx or CompileContext(),
            policy=policy,
            policy_file=loaded_policy_file,
            suppression=suppression,
            contract_evaluation=contract_evaluation,
            contract_mode=contract_mode,
        )

        # Run in a killable child process so a deep/hung scan that exceeds the
        # timeout is *terminated* (process + clang subtree) instead of orphaned to
        # keep burning CPU after the timeout response is sent (Codex review).
        try:
            payload = run_scan_subprocess(req, mcp_shared.MCP_TIMEOUT)
        except TimeoutError:
            elapsed = _time.monotonic() - t0
            _audit_log("abi_scan", {"binary": bin_path.name}, elapsed, "timeout")
            return json.dumps(
                {
                    "status": "error",
                    "error": f"abi_scan timed out after {mcp_shared.MCP_TIMEOUT}s",
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


# abi_deps / abi_aggregate / abi_project_validate / abi_project_plan live in
# the sibling module below (file-size split); imported here for side-effect
# (registers them via @mcp.tool()) and so they still resolve as
# ``abicheck.mcp_server.abi_deps`` etc. for existing callers/tests.
from .mcp_server_project import (  # noqa: E402,F401
    abi_aggregate,
    abi_deps,
    abi_project_plan,
    abi_project_validate,
)

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the abicheck MCP server (stdio transport)."""
    import argparse

    parser = argparse.ArgumentParser(description="abicheck MCP server")
    parser.add_argument(
        "--timeout",
        type=int,
        default=mcp_shared.MCP_TIMEOUT,
        help=f"Timeout in seconds for tool calls (default: {mcp_shared.MCP_TIMEOUT})",
    )
    parser.add_argument(
        "--max-file-size",
        type=int,
        default=mcp_shared.MCP_MAX_FILE_SIZE,
        help=f"Max input file size in bytes (default: {mcp_shared.MCP_MAX_FILE_SIZE})",
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
    # Module-qualified assignment (not `global`): MCP_TIMEOUT/MCP_MAX_FILE_SIZE/
    # _structured_logging are owned by mcp_shared so every tool module (not
    # just this one) observes the override — see mcp_shared's own docstring.
    mcp_shared.MCP_TIMEOUT = args.timeout
    mcp_shared.MCP_MAX_FILE_SIZE = args.max_file_size
    mcp_shared._structured_logging = args.log_format == "json"

    # Redirect logging to stderr to avoid corrupting stdio JSON-RPC
    handler = logging.StreamHandler(sys.stderr)
    if mcp_shared._structured_logging:
        handler.setFormatter(logging.Formatter("%(message)s"))
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s: %(name)s: %(message)s"))
    logger = logging.getLogger("abicheck")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
