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

"""ABICC compatibility CLI commands and helpers.

All ABICC-specific command logic lives here.
Core abicheck commands (dump/compare) remain in abicheck.cli.

Commands:
  abicheck compat check  — ABICC drop-in comparison (was ``abicheck compat``)
  abicheck compat dump   — dump from ABICC XML descriptor (was ``abicheck compat-dump``)
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from ..checker import compare
from ..dumper import dump
from ..html_report import write_html_report
from ..reporter import to_json, to_markdown
from ..serialization import load_snapshot, save_snapshot
from ._errors import (
    _classify_compat_error_exit_code,
    _classify_fs_error,
    _compat_fail,
    _is_compile_failure,
    _is_descriptor_or_suppression_context,
    _looks_like_missing_path_message,
    _looks_like_tool_missing,
)
from ._helpers import (  # noqa: F401
    _API_BREAK_KINDS as _API_BREAK_KINDS,
    _BINARY_ONLY_KINDS as _BINARY_ONLY_KINDS,
    _NEW_SYMBOL_KINDS as _NEW_SYMBOL_KINDS,
    _P2_STUB_FLAGS as _P2_STUB_FLAGS,
    _apply_strict as _apply_strict,
    _apply_warn_newsym as _apply_warn_newsym,
    _build_compat_suppression as _build_compat_suppression,
    _build_internal_suppression as _build_internal_suppression,
    _build_skip_suppression as _build_skip_suppression,
    _build_whitelist_suppression as _build_whitelist_suppression,
    _detect_compiler_version as _detect_compiler_version,
    _do_echo as _do_echo,
    _filter_binary_only as _filter_binary_only,
    _filter_source_only as _filter_source_only,
    _is_widening_return_type_change as _is_widening_return_type_change,
    _limit_affected_changes as _limit_affected_changes,
    _load_skip_headers as _load_skip_headers,
    _merge_suppression as _merge_suppression,
    _resolve_headers_from_list as _resolve_headers_from_list,
    _safe_path as _safe_path,
    _setup_logging as _setup_logging,
    _warn_stub_flags as _warn_stub_flags,
    _write_affected_list as _write_affected_list,
)
from .abicc_dump_import import (
    import_abicc_perl_dump,
    is_abicc_perl_dump_file,
    looks_like_perl_dump,
)
from .descriptor import parse_descriptor
from .xml_report import write_xml_report

# Re-exports for backwards compatibility (these used to be defined inline).
__all__ = [
    "_classify_compat_error_exit_code",
    "_classify_fs_error",
    "_compat_fail",
    "_is_compile_failure",
    "_is_descriptor_or_suppression_context",
    "_looks_like_missing_path_message",
    "_looks_like_tool_missing",
]

if TYPE_CHECKING:
    from ..checker import DiffResult
    from ..model import AbiSnapshot
    from .descriptor import CompatDescriptor


# ── compat group ──────────────────────────────────────────────────────────────


class _CompatGroup(click.Group):
    """Click Group that falls back to the 'check' subcommand when no subcommand is given.

    This preserves ABICC drop-in compatibility: ``abicheck compat -lib foo -old ...``
    behaves identically to ``abicheck compat check -lib foo -old ...``.
    """

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        # If first token is a known subcommand, let Click handle normally.
        if args and not args[0].startswith("-"):
            return super().parse_args(ctx, args)
        # Do NOT inject 'check' for bare --help/-h — show group help instead.
        if not args or args[0] in ("--help", "-h"):
            return super().parse_args(ctx, args)
        # Option-led invocation (e.g. -lib foo -old ...) → inject 'check'
        args = ["check", *args]
        return super().parse_args(ctx, args)


@click.group("compat", cls=_CompatGroup)
def compat_group() -> None:
    """ABICC-compatible commands (drop-in replacement for abi-compliance-checker).

    When called without a subcommand (e.g. ``abicheck compat -lib foo -old v1.xml -new v2.xml``),
    the ``check`` subcommand is invoked automatically for drop-in ABICC compatibility.
    """


_CROSS_COMPILATION_OPTIONS = (
    click.option(
        "-gcc-path",
        "-cross-gcc",
        "gcc_path",
        default=None,
        help="Path to GCC/G++ cross-compiler binary.",
    ),
    click.option(
        "-gcc-prefix",
        "-cross-prefix",
        "gcc_prefix",
        default=None,
        help="Cross-toolchain prefix (e.g. aarch64-linux-gnu-).",
    ),
    click.option(
        "-gcc-options",
        "gcc_options",
        default=None,
        help="Extra compiler flags passed through to castxml.",
    ),
    click.option(
        "-sysroot",
        "sysroot",
        default=None,
        type=click.Path(path_type=Path),
        help="Alternative system root directory.",
    ),
    click.option(
        "-nostdinc",
        "nostdinc",
        is_flag=True,
        default=False,
        help="Do not search standard system include paths.",
    ),
    click.option("-lang", "lang", default=None, help="Force language: C or C++."),
    click.option(
        "-arch", "arch", default=None, help="Target architecture (informational)."
    ),
)


def cross_compilation_options(f: Any) -> Any:
    """Apply the ABICC cross-compilation/toolchain flags shared by check and dump.

    One canonical definition keeps the two subcommands' flag spellings, defaults,
    and help text from drifting apart.
    """
    for opt in reversed(_CROSS_COMPILATION_OPTIONS):
        f = opt(f)
    return f


# ── compat dump subcommand ────────────────────────────────────────────────────


@compat_group.command("dump")
@click.option("-lib", "-l", "-library", "lib_name", required=True, help="Library name.")
@click.option(
    "-dump",
    "desc_path",
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Path to ABICC XML descriptor to dump.",
)
@click.option(
    "-dump-path",
    "dump_path",
    default=None,
    type=click.Path(path_type=Path),
    help="Output dump file path. Default: abi_dumps/<lib>/<version>/dump.json.",
)
@click.option(
    "-dump-format",
    "dump_format",
    default="json",
    help="Dump format. Only 'json' is supported (ABICC perl/xml not supported).",
)
@click.option("-vnum", "vnum", default=None, help="Override version label.")
# ── Cross-compilation flags ───────────────────────────────────────────────────
@cross_compilation_options
@click.option(
    "-relpath",
    "relpath",
    default=None,
    help="Replace {RELPATH} macros in descriptor paths.",
)
@click.option(
    "-q",
    "-quiet",
    "quiet",
    is_flag=True,
    default=False,
    help="Suppress console output.",
)
# ── P2 stub flags (accepted for compat, no effect) ───────────────────────────
@click.option("-sort", "sort_dump", is_flag=True, default=False, hidden=True)
@click.option("-extra-dump", "extra_dump", is_flag=True, default=False, hidden=True)
@click.option("-extra-info", "extra_info", default=None, hidden=True)
@click.option("-check", "check", is_flag=True, default=False, hidden=True)
@click.option("-xml", "xml_format", is_flag=True, default=False, hidden=True)
def compat_dump_cmd(
    lib_name: str,
    desc_path: Path,
    dump_path: Path | None,
    dump_format: str,
    vnum: str | None,
    gcc_path: str | None,
    gcc_prefix: str | None,
    gcc_options: str | None,
    sysroot: Path | None,
    nostdinc: bool,
    lang: str | None,
    arch: str | None,
    relpath: str | None,
    quiet: bool,
    # P2 stubs
    sort_dump: bool,
    extra_dump: bool,
    extra_info: str | None,
    check: bool,
    xml_format: bool,
) -> None:
    """Create an ABI dump from an ABICC XML descriptor (ABICC -dump equivalent).

    Produces a JSON ABI snapshot that can be used with ``abicheck compat check``
    or ``abicheck compare`` for later comparison. This enables two-stage CI
    workflows: dump once, compare later.

    \b
    Examples::
        # Create dump from descriptor:
        abicheck compat dump -lib libfoo -dump v1.xml

        # With explicit output path:
        abicheck compat dump -lib libfoo -dump v1.xml -dump-path libfoo-v1.json

        # Override version label:
        abicheck compat dump -lib libfoo -dump v1.xml -vnum 2025.1

        # Cross-compilation:
        abicheck compat dump -lib libfoo -dump v1.xml -gcc-prefix aarch64-linux-gnu-
    """
    _warn_stub_flags(
        quiet,
        sort_dump=sort_dump,
        extra_dump=extra_dump,
        extra_info=extra_info,
        check=check,
        xml_format=xml_format,
    )

    if dump_format.lower() not in ("json",):
        _do_echo(
            f"Warning: dump format '{dump_format}' is not supported. Using JSON.",
            quiet,
        )

    if arch:
        _do_echo(f"Note: -arch {arch} is recorded for informational purposes.", quiet)

    try:
        desc = parse_descriptor(desc_path, relpath=relpath)
    except (ValueError, FileNotFoundError, OSError) as exc:
        _compat_fail("parsing descriptor", exc)

    if vnum:
        from dataclasses import replace as _replace  # noqa: PLC0415

        desc = _replace(desc, version=vnum)

    so_path = desc.libs[0]
    if len(desc.libs) > 1:
        _do_echo(
            f"Warning: descriptor has {len(desc.libs)} <libs> entries; using first: {so_path}",
            quiet,
        )

    if not so_path.exists():
        click.echo(f"Error: library not found: {so_path}", err=True)
        sys.exit(2)

    try:
        snap = dump(
            so_path,
            headers=desc.headers,
            version=desc.version,
            gcc_path=gcc_path,
            gcc_prefix=gcc_prefix,
            gcc_options=gcc_options,
            sysroot=sysroot,
            nostdinc=nostdinc,
            lang=lang,
        )
    except Exception as exc:  # noqa: BLE001
        _compat_fail("during dump", exc)

    # Override library name to match -lib flag
    from dataclasses import replace as _replace  # noqa: PLC0415

    snap = _replace(snap, library=lib_name)

    if dump_path is None:
        dump_path = (
            Path("abi_dumps")
            / _safe_path(lib_name)
            / _safe_path(desc.version)
            / "dump.json"
        )

    dump_path.parent.mkdir(parents=True, exist_ok=True)
    save_snapshot(snap, dump_path)
    _do_echo(f"ABI dump written to {dump_path}", quiet)


# ── compat_check_cmd helpers ─────────────────────────────────────────────────


def _apply_result_transforms(
    result: DiffResult,
    *,
    warn_newsym: bool,
    limit_affected: int,
    source_only: bool,
    binary_only: bool,
    strict: bool,
    strict_mode: str,
) -> tuple[DiffResult, DiffResult]:
    """Apply post-compare transforms and return (transformed_result, full_result).

    full_result is the result before source-only filtering (used for split reports).
    The transforms are applied in order: warn-newsym, limit-affected, source-only filter, strict.
    """
    if warn_newsym:
        result = _apply_warn_newsym(result)
    if limit_affected > 0:
        result = _limit_affected_changes(result, limit_affected)

    # full_result is saved before source filtering for -bin-report-path / -src-report-path.
    full_result = result

    if source_only and not binary_only:
        result = _filter_source_only(result)
    if strict:
        result = _apply_strict(result, mode=strict_mode)

    return result, full_result


# ── compat compare subcommand ─────────────────────────────────────────────────


@compat_group.command("check")
# ── Core input flags ──────────────────────────────────────────────────────────
@click.option(
    "-lib",
    "-l",
    "-library",
    "lib_name",
    required=True,
    help="Library name (e.g. libdnnl).",
)
@click.option(
    "-old",
    "-d1",
    "old_desc",
    required=True,
    type=click.Path(path_type=Path),
    help="Path to old version ABICC XML descriptor or ABI dump.",
)
@click.option(
    "-new",
    "-d2",
    "-n",
    "new_desc",
    required=True,
    type=click.Path(path_type=Path),
    help="Path to new version ABICC XML descriptor or ABI dump.",
)
@click.option(
    "-d",
    "-f",
    "-filter",
    "filter_path",
    default=None,
    type=click.Path(path_type=Path),
    help="Path to XML descriptor with skip_* filtering rules.",
)
@click.option(
    "-p",
    "-params",
    "params_path",
    default=None,
    type=click.Path(path_type=Path),
    help="Path to parameters file (accepted for compat, informational).",
)
@click.option(
    "-app",
    "-application",
    "app_path",
    default=None,
    type=click.Path(path_type=Path),
    help="Application binary for portability checking (accepted for compat).",
)
# ── Report output flags ──────────────────────────────────────────────────────
@click.option(
    "-report-path",
    "report_path",
    default=None,
    type=click.Path(path_type=Path),
    help="Output report path.",
)
@click.option(
    "-bin-report-path",
    "bin_report_path",
    default=None,
    type=click.Path(path_type=Path),
    help="Separate binary-mode report output path.",
)
@click.option(
    "-src-report-path",
    "src_report_path",
    default=None,
    type=click.Path(path_type=Path),
    help="Separate source-mode report output path.",
)
@click.option(
    "-report-format",
    "fmt",
    default="html",
    type=click.Choice(["html", "htm", "xml", "json", "md"], case_sensitive=False),
    help="Report format (default: html). 'htm' is an alias for 'html'.",
)
@click.option(
    "--suppress",
    default=None,
    type=click.Path(path_type=Path),
    help="Suppression YAML file.",
)
# ── Analysis mode flags ──────────────────────────────────────────────────────
@click.option(
    "-s",
    "-strict",
    "strict",
    is_flag=True,
    default=False,
    help="Strict mode: any incompatible change is an error (exit 1).",
)
@click.option(
    "--strict-mode",
    "strict_mode",
    type=click.Choice(["full", "api"], case_sensitive=False),
    default="full",
    help="Strict promotion mode: 'full' (COMPATIBLE+API_BREAK->BREAKING, ABICC parity) "
    "or 'api' (only API_BREAK->BREAKING, COMPATIBLE stays COMPATIBLE). "
    "Only applies when -strict is also set.",
)
@click.option(
    "-show-retval",
    "show_retval",
    is_flag=True,
    default=False,
    help="Show return-value changes in report.",
)
@click.option(
    "-headers-only",
    "headers_only",
    is_flag=True,
    default=False,
    help="Header-only analysis mode (ELF/DWARF checks still run).",
)
@click.option(
    "-source",
    "-src",
    "-api",
    "source_only",
    is_flag=True,
    default=False,
    help="Check source (API) compatibility only.",
)
@click.option(
    "-binary",
    "-bin",
    "-abi",
    "binary_only",
    is_flag=True,
    default=False,
    help="Check binary (ABI) compatibility only (default).",
)
@click.option(
    "-warn-newsym",
    "warn_newsym",
    is_flag=True,
    default=False,
    help="Treat new symbols as compatibility breaks.",
)
@click.option(
    "-old-style",
    "-compat-html",
    "compat_html",
    is_flag=True,
    default=False,
    help="Generate ABICC-compatible HTML with matching element IDs and structure.",
)
@click.option(
    "-use-dumps",
    "use_dumps",
    is_flag=True,
    default=False,
    help="Interpret -old/-new as pre-built dumps (auto-detected).",
)
# ── Version label flags ──────────────────────────────────────────────────────
@click.option(
    "-v1",
    "-vnum1",
    "-version1",
    "vnum1",
    default=None,
    help="Override version label for old library.",
)
@click.option(
    "-v2",
    "-vnum2",
    "-version2",
    "vnum2",
    default=None,
    help="Override version label for new library.",
)
# ── Report presentation flags ────────────────────────────────────────────────
@click.option("-title", "title", default=None, help="Custom report title.")
@click.option(
    "-component", "component", default=None, help="Component name shown in report."
)
@click.option(
    "-limit-affected",
    "limit_affected",
    default=0,
    type=int,
    help="Max affected symbols shown per change kind.",
)
@click.option(
    "-list-affected",
    "list_affected",
    is_flag=True,
    default=False,
    help="Generate a separate file listing affected symbols.",
)
@click.option(
    "-stdout", "to_stdout", is_flag=True, default=False, help="Print report to stdout."
)
# ── Header filtering flags ───────────────────────────────────────────────────
@click.option(
    "-skip-headers",
    "skip_headers",
    default=None,
    type=click.Path(path_type=Path),
    help="File listing headers to exclude from analysis, one per line.",
)
@click.option(
    "-headers-list",
    "headers_list_path",
    default=None,
    type=click.Path(path_type=Path),
    help="File listing specific headers to include.",
)
@click.option(
    "-header", "single_header", default=None, help="Single header file to analyze."
)
# ── Symbol/type filtering flags ──────────────────────────────────────────────
@click.option(
    "-skip-symbols",
    "skip_symbols_path",
    default=None,
    type=click.Path(path_type=Path),
    help="File with symbols to skip (blacklist).",
)
@click.option(
    "-skip-types",
    "skip_types_path",
    default=None,
    type=click.Path(path_type=Path),
    help="File with types to skip (blacklist).",
)
@click.option(
    "-symbols-list",
    "symbols_list_path",
    default=None,
    type=click.Path(path_type=Path),
    help="File with symbols to check (whitelist).",
)
@click.option(
    "-types-list",
    "types_list_path",
    default=None,
    type=click.Path(path_type=Path),
    help="File with types to check (whitelist).",
)
@click.option(
    "-skip-internal-symbols",
    "skip_internal_symbols",
    default=None,
    help="Regex pattern for internal symbols to skip.",
)
@click.option(
    "-skip-internal-types",
    "skip_internal_types",
    default=None,
    help="Regex pattern for internal types to skip.",
)
@click.option(
    "-keep-cxx",
    "keep_cxx",
    is_flag=True,
    default=False,
    help="Include _ZS*, _ZNS*, _ZNKS* (C++ std) mangled symbols.",
)
@click.option(
    "-keep-reserved",
    "keep_reserved",
    is_flag=True,
    default=False,
    help="Report changes in reserved fields.",
)
# ── Cross-compilation / toolchain flags ──────────────────────────────────────
@cross_compilation_options
# ── Relpath flags ────────────────────────────────────────────────────────────
@click.option(
    "-relpath",
    "relpath",
    default=None,
    help="Replace {RELPATH} macros in both descriptor paths.",
)
@click.option(
    "-relpath1",
    "relpath1",
    default=None,
    help="Replace {RELPATH} macros in old descriptor paths.",
)
@click.option(
    "-relpath2",
    "relpath2",
    default=None,
    help="Replace {RELPATH} macros in new descriptor paths.",
)
# ── Logging flags ────────────────────────────────────────────────────────────
@click.option(
    "-q",
    "-quiet",
    "quiet",
    is_flag=True,
    default=False,
    help="Suppress console output.",
)
@click.option(
    "-log-path",
    "log_path",
    default=None,
    type=click.Path(path_type=Path),
    help="Redirect log output to file.",
)
@click.option(
    "-log1-path",
    "log1_path",
    default=None,
    type=click.Path(path_type=Path),
    help="Separate log path for old library analysis.",
)
@click.option(
    "-log2-path",
    "log2_path",
    default=None,
    type=click.Path(path_type=Path),
    help="Separate log path for new library analysis.",
)
@click.option(
    "-logging-mode",
    "logging_mode",
    default=None,
    help="Logging mode: 'w' (overwrite), 'a' (append), 'n' (none).",
)
# ── P2 stub flags (accepted for ABICC compat, no effect) ─────────────────────
@click.option(
    "-mingw-compatible", "mingw_compatible", is_flag=True, default=False, hidden=True
)
@click.option(
    "-cxx-incompatible",
    "-cpp-incompatible",
    "cxx_incompatible",
    is_flag=True,
    default=False,
    hidden=True,
)
@click.option(
    "-cpp-compatible", "cpp_compatible", is_flag=True, default=False, hidden=True
)
@click.option(
    "-static", "-static-libs", "static_libs", is_flag=True, default=False, hidden=True
)
@click.option("-ext", "-extended", "extended", is_flag=True, default=False, hidden=True)
@click.option("-quick", "quick", is_flag=True, default=False, hidden=True)
@click.option("-force", "force", is_flag=True, default=False, hidden=True)
@click.option("-check", "check", is_flag=True, default=False, hidden=True)
@click.option("-extra-info", "extra_info", default=None, hidden=True)
@click.option("-extra-dump", "extra_dump", is_flag=True, default=False, hidden=True)
@click.option("-sort", "sort_dump", is_flag=True, default=False, hidden=True)
@click.option("-xml", "xml_format", is_flag=True, default=False, hidden=True)
@click.option(
    "-skip-typedef-uncover",
    "skip_typedef_uncover",
    is_flag=True,
    default=False,
    hidden=True,
)
@click.option(
    "-check-private-abi", "check_private_abi", is_flag=True, default=False, hidden=True
)
@click.option(
    "-skip-unidentified", "skip_unidentified", is_flag=True, default=False, hidden=True
)
@click.option("-tolerance", "tolerance", default=None, hidden=True)
@click.option("-tolerant", "tolerant", is_flag=True, default=False, hidden=True)
@click.option(
    "-disable-constants-check",
    "disable_constants_check",
    is_flag=True,
    default=False,
    hidden=True,
)
@click.option(
    "-skip-added-constants",
    "skip_added_constants",
    is_flag=True,
    default=False,
    hidden=True,
)
@click.option(
    "-skip-removed-constants",
    "skip_removed_constants",
    is_flag=True,
    default=False,
    hidden=True,
)
@click.option("-count-symbols", "count_symbols", default=None, hidden=True)
@click.option("-count-all-symbols", "count_all_symbols", default=None, hidden=True)
def compat_check_cmd(  # noqa: PLR0913
    lib_name: str,
    old_desc: Path,
    new_desc: Path,
    filter_path: Path | None,
    params_path: Path | None,
    app_path: Path | None,
    report_path: Path | None,
    bin_report_path: Path | None,
    src_report_path: Path | None,
    fmt: str,
    suppress: Path | None,
    strict: bool,
    strict_mode: str,
    show_retval: bool,
    headers_only: bool,
    source_only: bool,
    binary_only: bool,
    warn_newsym: bool,
    compat_html: bool,
    use_dumps: bool,
    vnum1: str | None,
    vnum2: str | None,
    title: str | None,
    component: str | None,
    limit_affected: int,
    list_affected: bool,
    to_stdout: bool,
    skip_headers: Path | None,
    headers_list_path: Path | None,
    single_header: str | None,
    skip_symbols_path: Path | None,
    skip_types_path: Path | None,
    symbols_list_path: Path | None,
    types_list_path: Path | None,
    skip_internal_symbols: str | None,
    skip_internal_types: str | None,
    keep_cxx: bool,
    keep_reserved: bool,
    gcc_path: str | None,
    gcc_prefix: str | None,
    gcc_options: str | None,
    sysroot: Path | None,
    nostdinc: bool,
    lang: str | None,
    arch: str | None,
    relpath: str | None,
    relpath1: str | None,
    relpath2: str | None,
    quiet: bool,
    log_path: Path | None,
    log1_path: Path | None,
    log2_path: Path | None,
    logging_mode: str | None,
    # P2 stubs
    mingw_compatible: bool,
    cxx_incompatible: bool,
    cpp_compatible: bool,
    static_libs: bool,
    extended: bool,
    quick: bool,
    force: bool,
    check: bool,
    extra_info: str | None,
    extra_dump: bool,
    sort_dump: bool,
    xml_format: bool,
    skip_typedef_uncover: bool,
    check_private_abi: bool,
    skip_unidentified: bool,
    tolerance: str | None,
    tolerant: bool,
    disable_constants_check: bool,
    skip_added_constants: bool,
    skip_removed_constants: bool,
    count_symbols: str | None,
    count_all_symbols: str | None,
) -> None:
    """Drop-in replacement for abi-compliance-checker.

    Reads ABICC-format XML descriptors and produces an ABI compatibility report.
    Supports all ABICC flags for drop-in CI replacement.

    \b
    Exit codes mirror ABICC:
      0 - compatible or no change (NO_CHANGE, COMPATIBLE, COMPATIBLE_WITH_RISK)
          COMPATIBLE_WITH_RISK exits 0 - binary-compatible; risk is surfaced in report only.
          With -strict, it is promoted to exit 1.
      1 - breaking ABI change detected (BREAKING)
      2 - source-level break (API_BREAK)
      3-11 - classified compat-mode errors (best-effort mapping)

    Note: with -strict, API_BREAK is also promoted to exit 1.

    \b
    Examples::

        # Before:
        abi-compliance-checker -lib libfoo -old old.xml -new new.xml -report-path r.html

        # After:
        abicheck compat check -lib libdnnl -old old.xml -new new.xml -report-path r.html
    """
    # ── Setup logging ────────────────────────────────────────────────────
    try:
        _log1_handler, _log2_handler = _setup_logging(
            log_path, log1_path, log2_path, logging_mode, quiet
        )
    except OSError as exc:
        _compat_fail("setting up logging", exc)

    # ── Warn about P2 stub flags ─────────────────────────────────────────
    _warn_stub_flags(
        quiet,
        mingw_compatible=mingw_compatible,
        cxx_incompatible=cxx_incompatible,
        cpp_compatible=cpp_compatible,
        static_libs=static_libs,
        extended=extended,
        quick=quick,
        force=force,
        check=check,
        extra_info=extra_info,
        extra_dump=extra_dump,
        sort_dump=sort_dump,
        xml_format=xml_format,
        skip_typedef_uncover=skip_typedef_uncover,
        check_private_abi=check_private_abi,
        skip_unidentified=skip_unidentified,
        tolerance=tolerance,
        tolerant=tolerant,
        disable_constants_check=disable_constants_check,
        skip_added_constants=skip_added_constants,
        skip_removed_constants=skip_removed_constants,
    )

    _emit_compat_info_notes(
        quiet=quiet,
        compat_html=compat_html,
        use_dumps=use_dumps,
        filter_path=filter_path,
        params_path=params_path,
        app_path=app_path,
        arch=arch,
        keep_cxx=keep_cxx,
        keep_reserved=keep_reserved,
        count_symbols=count_symbols,
        count_all_symbols=count_all_symbols,
    )

    # ── Resolve relpath overrides, detect Perl dumps, parse descriptors ──
    old_d, new_d, _skip_headers_set = _load_compat_inputs(
        old_desc,
        new_desc,
        relpath,
        relpath1,
        relpath2,
        skip_headers,
        quiet,
    )

    old_snap, old_version, new_snap, new_version = _take_snapshots_with_logging(
        old_d,
        new_d,
        old_desc,
        new_desc,
        vnum1,
        vnum2,
        _log1_handler,
        _log2_handler,
        headers_list_path=headers_list_path,
        single_header=single_header,
        skip_headers_set=_skip_headers_set,
        quiet=quiet,
        gcc_path=gcc_path,
        gcc_prefix=gcc_prefix,
        gcc_options=gcc_options,
        sysroot=sysroot,
        nostdinc=nostdinc,
        lang=lang,
    )

    if headers_only:
        _do_echo("Note: -headers-only is accepted — ELF/DWARF checks still run.", quiet)

    suppression = _build_compat_suppression(
        skip_symbols_path,
        skip_types_path,
        symbols_list_path,
        types_list_path,
        skip_internal_symbols,
        skip_internal_types,
        suppress,
    )

    result = compare(old_snap, new_snap, suppression=suppression, policy="strict_abi")

    # ── Post-compare transforms ───────────────────────────────────────────
    result, full_result = _apply_result_transforms(
        result,
        warn_newsym=warn_newsym,
        limit_affected=limit_affected,
        source_only=source_only,
        binary_only=binary_only,
        strict=strict,
        strict_mode=strict_mode,
    )

    verdict = (
        result.verdict.value
        if hasattr(result.verdict, "value")
        else str(result.verdict)
    )

    # Normalize format aliases: htm → html
    if fmt.lower() == "htm":
        fmt = "html"

    # Build effective title
    effective_title = title
    if component and not effective_title:
        effective_title = f"ABI Compatibility Report — {lib_name} ({component})"

    # ── Determine report output path and write all reports ────────────────
    report_path = _resolve_report_path_and_mkdir(
        report_path, lib_name, old_version, new_version, fmt, quiet
    )

    _write_all_reports(
        result,
        full_result,
        report_path,
        bin_report_path,
        src_report_path,
        list_affected=list_affected,
        to_stdout=to_stdout,
        quiet=quiet,
        fmt=fmt,
        lib_name=lib_name,
        old_version=old_version,
        new_version=new_version,
        effective_title=effective_title,
        compat_html=compat_html,
        arch=arch,
        gcc_path=gcc_path,
    )

    _print_summary_and_exit(result, verdict, quiet, report_path)


# ── compat command-flow helpers (kept here so test monkeypatches on
#    abicheck.compat.cli.dump / parse_descriptor / compare resolve correctly) ──


def _emit_compat_info_notes(
    *,
    quiet: bool,
    compat_html: bool,
    use_dumps: bool,
    filter_path: Path | None,
    params_path: Path | None,
    app_path: Path | None,
    arch: str | None,
    keep_cxx: bool,
    keep_reserved: bool,
    count_symbols: str | None,
    count_all_symbols: str | None,
) -> None:
    """Emit informational notes for ABICC-compat flags with limited effect."""
    notes: list[str] = []
    if compat_html:
        notes.append(
            "Note: -compat-html / -old-style enabled: HTML will match ABICC element IDs."
        )
    if use_dumps:
        notes.append(
            "Note: -use-dumps is accepted; abicheck auto-detects JSON dumps by extension."
        )
    if filter_path:
        notes.append(
            f"Note: -filter {filter_path} is accepted for compatibility (not yet applied)."
        )
    if params_path:
        notes.append(
            f"Note: -params {params_path} is accepted for compatibility (not yet applied)."
        )
    if app_path:
        notes.append(
            f"Note: -app {app_path} is accepted for compatibility (not yet applied)."
        )
    if arch:
        notes.append(f"Note: -arch {arch} is recorded for informational purposes.")
    if keep_cxx:
        notes.append(
            "Note: -keep-cxx is accepted; abicheck includes all exported symbols by default."
        )
    if keep_reserved:
        notes.append(
            "Note: -keep-reserved is accepted; abicheck reports all field changes by default."
        )
    if count_symbols:
        notes.append(
            f"Note: -count-symbols {count_symbols} is accepted for compatibility (not yet applied)."
        )
    if count_all_symbols:
        notes.append(
            f"Note: -count-all-symbols {count_all_symbols} is accepted for compatibility (not yet applied)."
        )
    for note in notes:
        _do_echo(note, quiet)


def _parse_compat_descriptors(
    old_desc: Path,
    new_desc: Path,
    old_relpath: str | None,
    new_relpath: str | None,
) -> tuple[CompatDescriptor | AbiSnapshot, CompatDescriptor | AbiSnapshot]:
    """Parse old/new descriptors or dumps with compat-mode error mapping."""
    try:
        return (
            _load_descriptor_or_dump(old_desc, relpath=old_relpath),
            _load_descriptor_or_dump(new_desc, relpath=new_relpath),
        )
    except (ValueError, FileNotFoundError, OSError) as exc:
        _compat_fail("parsing descriptor", exc)


def _snapshot_from_compat_input(
    data: CompatDescriptor | AbiSnapshot,
    vnum_override: str | None,
    desc_path: Path,
    *,
    headers_list_path: Path | None,
    single_header: str | None,
    skip_headers_set: set[str],
    quiet: bool,
    gcc_path: str | None,
    gcc_prefix: str | None,
    gcc_options: str | None,
    sysroot: Path | None,
    nostdinc: bool,
    lang: str | None,
) -> tuple[AbiSnapshot, str]:
    """Convert compat input (descriptor or dump) into a concrete snapshot."""
    from ..model import AbiSnapshot as _AbiSnapshot

    if isinstance(data, _AbiSnapshot):
        if vnum_override:
            from dataclasses import replace as _replace

            return _replace(data, version=vnum_override), vnum_override
        return data, data.version
    desc = data
    if vnum_override:
        from dataclasses import replace as _replace

        desc = _replace(desc, version=vnum_override)
    so = desc.libs[0]
    if len(desc.libs) > 1:
        _do_echo(
            f"Warning: descriptor {desc_path.name} has {len(desc.libs)} <libs> entries; "
            f"using only the first: {so}",
            quiet,
        )
    hdrs = _resolve_headers_from_list(
        headers_list_path,
        single_header,
        desc.headers,
        skip_headers=skip_headers_set or None,
    )
    if not so.exists():
        _compat_fail(
            "accessing input files", FileNotFoundError(f"library not found: {so}")
        )
    snap = dump(
        so,
        headers=hdrs,
        version=desc.version,
        gcc_path=gcc_path,
        gcc_prefix=gcc_prefix,
        gcc_options=gcc_options,
        sysroot=sysroot,
        nostdinc=nostdinc,
        lang=lang,
    )
    return snap, desc.version


def _load_descriptor_or_dump(
    path: Path, *, relpath: str | None = None
) -> CompatDescriptor | AbiSnapshot:
    """Load either an ABICC XML descriptor or a JSON ABI dump.

    Returns:
        CompatDescriptor for XML descriptor files, AbiSnapshot for JSON dumps.

    Raises:
        ValueError: If the file is an ABICC Perl dump (unsupported format).
    """
    # ABICC Perl dump support (minimal migration-focused importer)
    if path.suffix == ".dump":
        return import_abicc_perl_dump(path)

    # Heuristic: if the file is JSON, load as a dump
    if path.suffix == ".json":
        return load_snapshot(path)

    # For XML files, peek at content to detect ABICC Perl dump disguised as .xml
    # (ABICC -dump-format xml produces a different XML schema than descriptors)
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:512]
    except OSError:
        head = ""

    # Detect ABICC Perl Data::Dumper format (starts with $VAR1 = { or similar)
    if looks_like_perl_dump(head):
        return import_abicc_perl_dump(path)

    # Detect ABICC XML dump format (contains <ABI_dump_* or <abi_dump tags)
    if "<ABI_dump" in head or "<abi_dump" in head or "ABI_COMPLIANCE_CHECKER" in head:
        raise ValueError(
            f"ABICC XML dump format detected: {path}\n"
            "  abicheck currently supports ABICC Perl Data::Dumper dumps, not ABICC XML dumps.\n"
            "  If possible, generate the default ABI.dump (Perl) format with abi-dumper,\n"
            "  or convert via descriptor using 'abicheck compat dump' to abicheck JSON."
        )

    # Otherwise parse as XML descriptor
    return parse_descriptor(path, relpath=relpath)


def _load_compat_inputs(
    old_desc: Path,
    new_desc: Path,
    relpath: str | None,
    relpath1: str | None,
    relpath2: str | None,
    skip_headers: Path | None,
    quiet: bool,
) -> tuple[CompatDescriptor | AbiSnapshot, CompatDescriptor | AbiSnapshot, set[str]]:
    """Resolve relpath overrides, notify about Perl dumps, parse descriptors, load skip-headers set.

    Returns (old_d, new_d, skip_headers_set).
    """
    old_relpath = relpath1 or relpath
    new_relpath = relpath2 or relpath

    old_is_abicc_perl = is_abicc_perl_dump_file(old_desc)
    new_is_abicc_perl = is_abicc_perl_dump_file(new_desc)
    if old_is_abicc_perl or new_is_abicc_perl:
        _do_echo(
            "Info: ABICC Perl ABI.dump input detected. "
            "Using migration-focused importer (full ABICC dump parity is not guaranteed). "
            "Prefer abicheck JSON dumps for best fidelity.",
            quiet,
        )

    old_d, new_d = _parse_compat_descriptors(
        old_desc, new_desc, old_relpath, new_relpath
    )
    skip_headers_set = _load_skip_headers(skip_headers)
    if skip_headers_set:
        _do_echo(
            f"Applying -skip-headers: excluding {len(skip_headers_set)} header(s).",
            quiet,
        )

    return old_d, new_d, skip_headers_set


def _take_snapshots_with_logging(
    old_d: CompatDescriptor | AbiSnapshot,
    new_d: CompatDescriptor | AbiSnapshot,
    old_desc: Path,
    new_desc: Path,
    vnum1: str | None,
    vnum2: str | None,
    log1_handler: logging.Handler | None,
    log2_handler: logging.Handler | None,
    *,
    headers_list_path: Path | None,
    single_header: str | None,
    skip_headers_set: set[str],
    quiet: bool,
    gcc_path: str | None,
    gcc_prefix: str | None,
    gcc_options: str | None,
    sysroot: Path | None,
    nostdinc: bool,
    lang: str | None,
) -> tuple[AbiSnapshot, str, AbiSnapshot, str]:
    """Build old and new snapshots, activating per-phase log handlers around each dump call.

    Returns (old_snap, old_version, new_snap, new_version).
    Cleans up handlers on error before re-raising via _compat_fail.
    """
    _logger = logging.getLogger("abicheck")
    try:
        if log1_handler is not None:
            _logger.addHandler(log1_handler)
        old_snap, old_version = _snapshot_from_compat_input(
            old_d,
            vnum1,
            old_desc,
            headers_list_path=headers_list_path,
            single_header=single_header,
            skip_headers_set=skip_headers_set,
            quiet=quiet,
            gcc_path=gcc_path,
            gcc_prefix=gcc_prefix,
            gcc_options=gcc_options,
            sysroot=sysroot,
            nostdinc=nostdinc,
            lang=lang,
        )
        if log1_handler is not None:
            _logger.removeHandler(log1_handler)
            log1_handler.close()

        if log2_handler is not None:
            _logger.addHandler(log2_handler)
        new_snap, new_version = _snapshot_from_compat_input(
            new_d,
            vnum2,
            new_desc,
            headers_list_path=headers_list_path,
            single_header=single_header,
            skip_headers_set=skip_headers_set,
            quiet=quiet,
            gcc_path=gcc_path,
            gcc_prefix=gcc_prefix,
            gcc_options=gcc_options,
            sysroot=sysroot,
            nostdinc=nostdinc,
            lang=lang,
        )
        if log2_handler is not None:
            _logger.removeHandler(log2_handler)
            log2_handler.close()
    except Exception as exc:  # noqa: BLE001
        if log1_handler is not None:
            log1_handler.close()
        if log2_handler is not None:
            log2_handler.close()
        _compat_fail("during dump", exc)

    return old_snap, old_version, new_snap, new_version


def _resolve_report_path_and_mkdir(
    report_path: Path | None,
    lib_name: str,
    old_version: str,
    new_version: str,
    fmt: str,
    quiet: bool,
) -> Path:
    """Derive a default report path when none is given, then create parent directories.

    Returns the resolved Path.
    """
    if report_path is None:
        ext = fmt.lower()
        report_path = (
            Path("compat_reports")
            / _safe_path(lib_name)
            / f"{_safe_path(old_version)}_to_{_safe_path(new_version)}"
            / f"compat_report.{ext}"
        )
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _compat_fail("writing report output", exc)
    return report_path


def _generate_compat_report(
    r: DiffResult,
    path: Path,
    *,
    fmt: str,
    lib_name: str,
    old_version: str,
    new_version: str,
    effective_title: str | None,
    compat_html: bool,
    arch: str | None,
    gcc_path: str | None,
) -> None:
    """Write a single report file in the requested format."""
    if fmt == "html":
        write_html_report(
            r,
            output_path=path,
            lib_name=lib_name,
            old_version=old_version,
            new_version=new_version,
            old_symbol_count=r.old_symbol_count,
            title=effective_title,
            compat_html=compat_html,
        )
    elif fmt == "xml":
        write_xml_report(
            r,
            output_path=path,
            lib_name=lib_name,
            old_version=old_version,
            new_version=new_version,
            old_symbol_count=r.old_symbol_count,
            arch=arch or "",
            compiler=_detect_compiler_version(gcc_path),
        )
    elif fmt == "json":
        path.write_text(to_json(r), encoding="utf-8")
    else:
        path.write_text(to_markdown(r), encoding="utf-8")


def _write_all_reports(
    result: DiffResult,
    full_result: DiffResult,
    report_path: Path,
    bin_report_path: Path | None,
    src_report_path: Path | None,
    *,
    list_affected: bool,
    to_stdout: bool,
    quiet: bool,
    fmt: str,
    lib_name: str,
    old_version: str,
    new_version: str,
    effective_title: str | None,
    compat_html: bool,
    arch: str | None,
    gcc_path: str | None,
) -> None:
    """Write primary report, optional split reports, affected-symbols list, and stdout echo."""
    _report_kwargs: dict[str, Any] = dict(
        fmt=fmt,
        lib_name=lib_name,
        old_version=old_version,
        new_version=new_version,
        effective_title=effective_title,
        compat_html=compat_html,
        arch=arch,
        gcc_path=gcc_path,
    )
    try:
        _generate_compat_report(result, report_path, **_report_kwargs)

        if bin_report_path:
            bin_report_path.parent.mkdir(parents=True, exist_ok=True)
            _generate_compat_report(
                _filter_binary_only(full_result), bin_report_path, **_report_kwargs
            )
            _do_echo(f"Binary report: {bin_report_path}", quiet)

        if src_report_path:
            src_report_path.parent.mkdir(parents=True, exist_ok=True)
            _generate_compat_report(
                _filter_source_only(full_result), src_report_path, **_report_kwargs
            )
            _do_echo(f"Source report: {src_report_path}", quiet)

        if list_affected:
            affected_path = report_path.with_suffix(".affected.txt")
            _write_affected_list(result, affected_path)
            _do_echo(f"Affected symbols: {affected_path}", quiet)

        if to_stdout:
            click.echo(report_path.read_text(encoding="utf-8"))
    except OSError as exc:
        _compat_fail("writing report output", exc)


def _print_summary_and_exit(
    result: DiffResult,
    verdict: str,
    quiet: bool,
    report_path: Path,
) -> None:
    """Print ABICC-style console summary and exit with the appropriate code."""
    from ..report_summary import compatibility_metrics  # noqa: PLC0415

    metrics = compatibility_metrics(result.changes, result.old_symbol_count)
    _do_echo(f"Binary compatibility: {metrics.binary_compatibility_pct:.1f}%", quiet)
    _do_echo(
        f"Total binary compatibility problems: {metrics.breaking_count}, warnings: 0",
        quiet,
    )
    _do_echo(f"Verdict: {verdict}", quiet)
    _do_echo(f"Report:  {report_path}", quiet)

    if verdict == "BREAKING":
        sys.exit(1)
    if verdict == "API_BREAK":
        sys.exit(2)
