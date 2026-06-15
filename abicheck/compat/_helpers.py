# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""Plain (non-Click) helper functions for the ABICC-compatible CLI.

Split out of ``compat/cli.py`` to keep that module under the AI-readiness
file-size limit. This module is a leaf: it must NOT import from
``compat.cli``. ``compat/cli.py`` re-imports these names so existing
``from abicheck.compat.cli import <helper>`` callers keep working.
"""

from __future__ import annotations

import logging
import re as _re
from pathlib import Path
from typing import TYPE_CHECKING

import click

from ..checker import ChangeKind
from ..checker_policy import (
    API_BREAK_KINDS as _POLICY_API_BREAK_KINDS,
    compute_verdict as _compute_verdict,
)
from ._errors import _compat_fail

if TYPE_CHECKING:
    from ..checker import DiffResult
    from ..suppression import SuppressionList


# ── ABICC compat helpers ──────────────────────────────────────────────────────


def _build_skip_suppression(
    skip_symbols_path: Path | None,
    skip_types_path: Path | None,
) -> SuppressionList:
    """Build a SuppressionList from ABICC-style -skip-symbols / -skip-types files.

    Both symbol and type names are stored as symbol-match suppressions — abicheck
    uses the type name as the symbol field for type-level changes (e.g. TYPE_REMOVED).

    Raises ValueError if a file contains an invalid regex pattern.
    Raises OSError if a file cannot be read.
    """
    from ..suppression import Suppression, SuppressionList  # noqa: PLC0415

    rules: list[Suppression] = []
    for _label, fpath in [("symbols", skip_symbols_path), ("types", skip_types_path)]:
        if fpath is None:
            continue
        names = [
            ln.strip()
            for ln in fpath.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")
        ]
        for name in names:
            # Suppression.__post_init__ validates regex — ValueError propagates to caller
            if any(c in name for c in ("*", "?", ".", "[")):
                rules.append(Suppression(symbol_pattern=name))
            else:
                rules.append(Suppression(symbol=name))
                # ABICC -skip-symbols commonly contains plain C function names
                # (e.g. "sub"), but our compare pipeline stores Itanium-mangled
                # symbols (e.g. "_Z3subii"). Add a fallback pattern only when the
                # name looks like a plain identifier (not already mangled, not a
                # type/struct name — identifiers starting with uppercase are likely
                # types and already matched by exact symbol= above).
                if (
                    name.isidentifier()
                    and not name.startswith("_Z")
                    and name[0].islower()
                ):
                    rules.append(Suppression(symbol_pattern=rf"_Z\d+{name}.*"))
    return SuppressionList(suppressions=rules)


def _build_whitelist_suppression(
    symbols_list_path: Path | None,
    types_list_path: Path | None,
) -> SuppressionList:
    """Build a SuppressionList that suppresses everything NOT in the whitelist.

    Inverts the whitelist into a regex-based suppression: any symbol/type not
    matching one of the whitelist entries is suppressed.

    Symbol and type whitelists are scoped independently: a symbol whitelist only
    affects symbol-level changes, and a type whitelist only affects type-level
    changes.  Names are preserved as-is (regex/glob syntax is not escaped).

    This is the inverse of -skip-symbols / -skip-types.
    """
    from ..suppression import Suppression, SuppressionList  # noqa: PLC0415

    rules: list[Suppression] = []

    # -symbols-list: whitelist scoped to symbol_pattern (function/variable changes)
    if symbols_list_path is not None:
        names = [
            ln.strip()
            for ln in symbols_list_path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")
        ]
        if names:
            # Pattern matches anything that is NOT one of the whitelisted names.
            # Names are not escaped — regex/glob syntax is preserved.
            negate_pattern = f"(?!({'|'.join(names)})$).*"
            rules.append(Suppression(symbol_pattern=negate_pattern))

    # -types-list: whitelist scoped to type_pattern (type/enum/typedef changes only)
    if types_list_path is not None:
        names = [
            ln.strip()
            for ln in types_list_path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")
        ]
        if names:
            negate_pattern = f"(?!({'|'.join(names)})$).*"
            rules.append(Suppression(type_pattern=negate_pattern))

    return SuppressionList(suppressions=rules)


def _build_internal_suppression(
    skip_internal_symbols: str | None,
    skip_internal_types: str | None,
) -> SuppressionList:
    """Build a SuppressionList from -skip-internal-symbols / -skip-internal-types regex patterns."""
    from ..suppression import Suppression, SuppressionList  # noqa: PLC0415

    rules: list[Suppression] = []
    if skip_internal_symbols is not None:
        rules.append(Suppression(symbol_pattern=skip_internal_symbols))
    if skip_internal_types is not None:
        rules.append(Suppression(type_pattern=skip_internal_types))
    return SuppressionList(suppressions=rules)


# API_BREAK-only ChangeKinds (source API breaks, not binary ABI breaks).
# Keep this aligned with checker policy as single source of truth.
_API_BREAK_KINDS: frozenset[ChangeKind] = frozenset(_POLICY_API_BREAK_KINDS)

# ELF/binary-only ChangeKinds (excluded in -source mode)
_BINARY_ONLY_KINDS: frozenset[ChangeKind] = frozenset(
    {
        ChangeKind.SONAME_CHANGED,
        ChangeKind.NEEDED_ADDED,
        ChangeKind.NEEDED_REMOVED,
        ChangeKind.RPATH_CHANGED,
        ChangeKind.RUNPATH_CHANGED,
        ChangeKind.SYMBOL_BINDING_CHANGED,
        ChangeKind.SYMBOL_BINDING_STRENGTHENED,
        ChangeKind.SYMBOL_TYPE_CHANGED,
        ChangeKind.SYMBOL_SIZE_CHANGED,
        # ELF st_size signal on an internal-looking data symbol — binary-only, just
        # like SYMBOL_SIZE_CHANGED, so it is filtered from source-only views too.
        ChangeKind.SYMBOL_SIZE_CHANGED_INTERNAL,
        # Header evidence may name the const object precisely, but the changed
        # value is still ELF st_size metadata rather than a source API delta.
        ChangeKind.SYMBOL_SIZE_CHANGED_CONST_OBJECT,
        ChangeKind.IFUNC_INTRODUCED,
        ChangeKind.IFUNC_REMOVED,
        ChangeKind.COMMON_SYMBOL_RISK,
        ChangeKind.SYMBOL_VERSION_DEFINED_REMOVED,
        # NOTE: SYMBOL_VERSION_REQUIRED_ADDED is now RISK_KINDS (COMPATIBLE_WITH_RISK verdict),
        # but it remains here because it is an ELF/binary-only signal (not visible in source
        # analysis). _filter_source_only re-derives verdict via compute_verdict() after
        # filtering, so RISK classification is preserved correctly.
        ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED,
        ChangeKind.SYMBOL_VERSION_REQUIRED_REMOVED,
        ChangeKind.DWARF_INFO_MISSING,
        ChangeKind.TOOLCHAIN_FLAG_DRIFT,
        ChangeKind.VECTOR_ABI_CHANGED,
        # DWARF-derived aggregate-return convention flip — a binary-only ABI fact
        # with no source-API delta, so it is filtered from source-only views.
        ChangeKind.STRUCT_RETURN_CONVENTION_CHANGED,
    }
)

# ChangeKinds that represent new symbols being added (for -warn-newsym)
_NEW_SYMBOL_KINDS: frozenset[ChangeKind] = frozenset(
    {
        ChangeKind.FUNC_ADDED,
        ChangeKind.VAR_ADDED,
    }
)

# P2 stub flags — accepted for ABICC CLI compatibility but have no effect.
# Each maps to (param_name, help_text).
_P2_STUB_FLAGS: dict[str, str] = {
    "mingw_compatible": "-mingw-compatible: MinGW ABI mode (accepted, no effect)",
    "cxx_incompatible": "-cxx-incompatible: C++ incompatibility mode (accepted, no effect)",
    "cpp_compatible": "-cpp-compatible: C++ compatibility mode (accepted, no effect)",
    "static_libs": "-static: static library analysis (accepted, no effect)",
    "extended": "-ext/-extended: extended analysis mode (accepted, no effect)",
    "quick": "-quick: quick analysis mode (accepted, no effect)",
    "force": "-force: force analysis (accepted, no effect)",
    "check": "-check: dump validity check (accepted, no effect)",
    "extra_info": "-extra-info: extra analysis output directory (accepted, no effect)",
    "extra_dump": "-extra-dump: extended dump (accepted, no effect)",
    "sort_dump": "-sort: sort dump output (accepted, no effect)",
    "xml_format": "-xml: XML dump format (accepted, no effect)",
    "skip_typedef_uncover": "-skip-typedef-uncover: skip typedef uncovering (accepted, no effect)",
    "check_private_abi": "-check-private-abi: check private ABI (accepted, no effect)",
    "skip_unidentified": "-skip-unidentified: skip unidentified headers (accepted, no effect)",
    "tolerance": "-tolerance: header parsing tolerance (accepted, no effect)",
    "tolerant": "-tolerant: enable all tolerance levels (accepted, no effect)",
    "disable_constants_check": "-disable-constants-check: skip constant checking (accepted, no effect)",
    "skip_added_constants": "-skip-added-constants: skip new constants (accepted, no effect)",
    "skip_removed_constants": "-skip-removed-constants: skip removed constants (accepted, no effect)",
}


def _apply_strict(result: DiffResult, *, mode: str = "full") -> DiffResult:
    """Apply strict-mode verdict promotion.

    mode='full': COMPATIBLE and API_BREAK → BREAKING (matches ABICC -strict behaviour).
                 Exception: pure additions (FUNC_ADDED, VAR_ADDED, TYPE_ADDED, etc.)
                 stay COMPATIBLE even in full mode, matching ABICC 2.3 semantics.
    mode='api':  only API_BREAK → BREAKING; COMPATIBLE stays COMPATIBLE.
                 Use when you want strict enforcement of API contract changes
                 but still allow purely additive changes.
    """
    from dataclasses import replace  # noqa: PLC0415

    from ..checker import Verdict  # noqa: PLC0415
    from ..checker_policy import ChangeKind  # noqa: PLC0415

    # ABICC semantics: pure additions remain COMPATIBLE even under -strict.
    # Only incompatible changes (removals, type changes, etc.) are promoted.
    _ADDITION_ONLY_KINDS: frozenset[ChangeKind] = frozenset(
        {
            ChangeKind.FUNC_ADDED,
            ChangeKind.VAR_ADDED,
            ChangeKind.TYPE_ADDED,
            ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE,
            ChangeKind.ENUM_MEMBER_ADDED,
            ChangeKind.UNION_FIELD_ADDED,
            ChangeKind.SYMBOL_VERSION_DEFINED_ADDED,
            ChangeKind.CONSTANT_ADDED,
            ChangeKind.NEEDED_ADDED,
        }
    )

    # COMPATIBLE_WITH_RISK is promoted to BREAKING in full strict mode:
    # it indicates a deployment-environment risk that the caller has opted-in
    # to treating as a hard failure. In 'api' mode it is left as-is because
    # it is binary-compatible — only the deployment environment is affected.
    verdicts_to_promote = (
        {"COMPATIBLE", "COMPATIBLE_WITH_RISK", "API_BREAK"}
        if mode == "full"
        else {"API_BREAK"}
    )
    if result.verdict.value in verdicts_to_promote:
        # In full mode, don't promote COMPATIBLE if the only changes are
        # pure additions — matches ABICC 2.3 behaviour where -strict keeps
        # additive-only changes as compatible (rc=0).
        if mode == "full" and result.verdict.value == "COMPATIBLE":
            all_kinds = {c.kind for c in result.changes}
            if all_kinds and all_kinds <= _ADDITION_ONLY_KINDS:
                return result  # pure additions stay COMPATIBLE
        return replace(result, verdict=Verdict.BREAKING)
    return result


def _is_widening_return_type_change(change: object) -> bool:
    """Check if a FUNC_RETURN_CHANGED is a widening conversion.

    Widening conversions (int→long, short→int, float→double, etc.) are
    source-compatible — callers can accept a wider return type without
    code changes.
    """
    from ..checker_policy import ChangeKind  # noqa: PLC0415

    if getattr(change, "kind", None) != ChangeKind.FUNC_RETURN_CHANGED:
        return False
    old_val = (getattr(change, "old_value", "") or "").strip()
    new_val = (getattr(change, "new_value", "") or "").strip()
    _WIDENING_PAIRS: set[tuple[str, str]] = {
        ("int", "long"),
        ("int", "long int"),
        ("int", "long long"),
        ("int", "long long int"),
        ("short", "int"),
        ("short", "long"),
        ("short int", "int"),
        ("short int", "long"),
        ("char", "short"),
        ("char", "int"),
        ("float", "double"),
        ("float", "long double"),
        ("double", "long double"),
        ("unsigned int", "unsigned long"),
        ("unsigned int", "unsigned long int"),
        ("unsigned short", "unsigned int"),
        ("unsigned char", "unsigned int"),
        ("unsigned char", "unsigned short"),
    }
    return (old_val, new_val) in _WIDENING_PAIRS


def _filter_source_only(result: DiffResult) -> DiffResult:
    """Remove binary-only changes from result for -source mode.

    Re-derives the verdict and propagates result.policy so that the returned
    DiffResult is fully self-consistent (verdict, .breaking, .source_breaks,
    .compatible all use the same policy).
    """
    from ..checker import DiffResult  # noqa: PLC0415

    policy = result.policy
    filtered = [
        c
        for c in result.changes
        if c.kind not in _BINARY_ONLY_KINDS
        # In source mode, widening return-type changes (int→long, etc.) are
        # source-compatible.  ABICC 2.3 treats them as warning-level (rc=0).
        # Exclude them entirely so verdict and change list stay consistent.
        and not _is_widening_return_type_change(c)
    ]
    verdict = _compute_verdict(filtered, policy=policy)

    return DiffResult(
        old_version=result.old_version,
        new_version=result.new_version,
        library=result.library,
        changes=filtered,
        verdict=verdict,
        suppressed_count=result.suppressed_count,
        suppressed_changes=result.suppressed_changes,
        suppression_file_provided=result.suppression_file_provided,
        policy=policy,
        old_symbol_count=result.old_symbol_count,
    )


def _filter_binary_only(result: DiffResult) -> DiffResult:
    """Remove source-only changes from result for -binary mode.

    Re-derives the verdict and propagates result.policy so that the returned
    DiffResult is fully self-consistent (verdict, .breaking, .source_breaks,
    .compatible all use the same policy).
    """
    from ..checker import DiffResult  # noqa: PLC0415

    policy = result.policy
    filtered = [c for c in result.changes if c.kind not in _API_BREAK_KINDS]
    verdict = _compute_verdict(filtered, policy=policy)

    return DiffResult(
        old_version=result.old_version,
        new_version=result.new_version,
        library=result.library,
        changes=filtered,
        verdict=verdict,
        suppressed_count=result.suppressed_count,
        suppressed_changes=result.suppressed_changes,
        suppression_file_provided=result.suppression_file_provided,
        policy=policy,
        old_symbol_count=result.old_symbol_count,
    )


def _apply_warn_newsym(result: DiffResult) -> DiffResult:
    """Promote new-symbol additions to BREAKING when -warn-newsym is set."""
    from ..checker import DiffResult, Verdict  # noqa: PLC0415

    has_new = any(c.kind in _NEW_SYMBOL_KINDS for c in result.changes)
    # Include COMPATIBLE_WITH_RISK: if the library adds a new symbol alongside a RISK_KINDS
    # change, the verdict may be COMPATIBLE_WITH_RISK. The user opted into -warn-newsym to
    # treat any new symbol as a hard failure — that intent applies regardless of concurrent
    # deployment-risk changes.
    if has_new and result.verdict.value in (
        "COMPATIBLE",
        "COMPATIBLE_WITH_RISK",
        "NO_CHANGE",
        "API_BREAK",
    ):
        return DiffResult(
            old_version=result.old_version,
            new_version=result.new_version,
            library=result.library,
            changes=result.changes,
            verdict=Verdict.BREAKING,
            suppressed_count=result.suppressed_count,
            suppressed_changes=result.suppressed_changes,
            suppression_file_provided=result.suppression_file_provided,
            policy=result.policy,
            old_symbol_count=result.old_symbol_count,
        )
    return result


def _limit_affected_changes(result: DiffResult, limit: int) -> DiffResult:
    """Limit the number of reported changes per unique ChangeKind."""
    from ..checker import Change, DiffResult  # noqa: PLC0415

    if limit <= 0:
        return result

    counts: dict[ChangeKind, int] = {}
    filtered: list[Change] = []
    for c in result.changes:
        cnt = counts.get(c.kind, 0)
        if cnt < limit:
            filtered.append(c)
        counts[c.kind] = cnt + 1

    return DiffResult(
        old_version=result.old_version,
        new_version=result.new_version,
        library=result.library,
        changes=filtered,
        verdict=result.verdict,
        suppressed_count=result.suppressed_count,
        suppressed_changes=result.suppressed_changes,
        suppression_file_provided=result.suppression_file_provided,
        policy=result.policy,
        old_symbol_count=result.old_symbol_count,
    )


def _write_affected_list(result: DiffResult, output_path: Path) -> None:
    """Write a newline-separated file of affected symbols."""
    symbols = sorted({c.symbol for c in result.changes if c.symbol})
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "\n".join(symbols) + "\n" if symbols else "", encoding="utf-8"
    )


def _safe_path(v: str) -> str:
    return _re.sub(r"[^\w.\-]", "_", v)


def _merge_suppression(
    base: SuppressionList | None, extra: SuppressionList
) -> SuppressionList:
    """Merge two suppression lists, handling None base."""
    from ..suppression import SuppressionList as SL  # noqa: PLC0415

    if base is not None:
        return SL.merge(base, extra)
    return extra


def _do_echo(msg: str, quiet: bool, *, err: bool = True) -> None:
    """Echo a message unless quiet mode is active."""
    if not quiet:
        click.echo(msg, err=err)


def _detect_compiler_version(gcc_path: str | None = None) -> str:
    """Detect GCC version for ABICC XML report <gcc> element."""
    import shutil
    import subprocess as _sp

    compiler = gcc_path or shutil.which("gcc") or shutil.which("cc") or ""
    if not compiler:
        return ""
    try:
        r = _sp.run(
            [compiler, "-dumpversion"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, _sp.TimeoutExpired):
        return ""


def _setup_logging(
    log_path: Path | None,
    log1_path: Path | None,
    log2_path: Path | None,
    logging_mode: str | None,
    quiet: bool,
) -> tuple[logging.FileHandler | None, logging.FileHandler | None]:
    """Configure logging based on ABICC-style log flags.

    -log-path: shared handler attached immediately.
    -log1-path / -log2-path: per-phase handlers returned (not yet attached)
    so the caller can activate them around the old/new dump phases.

    Returns (log1_handler, log2_handler) — either may be None.
    ``-logging-mode n`` disables file handlers entirely.
    """
    logger = logging.getLogger("abicheck")

    # Close and remove any existing FileHandlers to avoid leaking open files
    # when _setup_logging is called multiple times.
    for existing in list(logger.handlers):
        if isinstance(existing, logging.FileHandler):
            existing.close()
            logger.removeHandler(existing)

    if quiet:
        logger.setLevel(logging.WARNING)

    # -logging-mode n: no file handlers
    if logging_mode == "n":
        return None, None

    mode = "a" if logging_mode == "a" else "w"
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    any_handler = False

    def _make_handler(p: Path) -> logging.FileHandler:
        p.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(str(p), mode=mode, encoding="utf-8")
        handler.setFormatter(fmt)
        return handler

    # Shared log: attach immediately
    if log_path is not None:
        logger.addHandler(_make_handler(log_path))
        any_handler = True

    # Per-phase handlers: create but do NOT attach yet
    log1_handler = _make_handler(log1_path) if log1_path is not None else None
    log2_handler = _make_handler(log2_path) if log2_path is not None else None

    if (any_handler or log1_handler or log2_handler) and not quiet:
        logger.setLevel(logging.DEBUG)

    return log1_handler, log2_handler


def _load_skip_headers(skip_headers_path: Path | None) -> set[str]:
    """Load a set of header names/paths to exclude from analysis."""
    if skip_headers_path is None:
        return set()
    lines = [
        ln.strip()
        for ln in skip_headers_path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    return set(lines)


def _resolve_headers_from_list(
    headers_list_path: Path | None,
    single_header: str | None,
    base_headers: list[Path],
    *,
    skip_headers: set[str] | None = None,
) -> list[Path]:
    """Merge headers from -headers-list file and -header flag with descriptor headers."""
    result = list(base_headers)

    if headers_list_path is not None:
        list_base = headers_list_path.parent
        lines = [
            ln.strip()
            for ln in headers_list_path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")
        ]
        for line in lines:
            p = Path(line)
            # Resolve relative paths against the list file's directory
            if not p.is_absolute():
                p = list_base / p
            if p.exists():
                result.append(p)

    if single_header is not None:
        p = Path(single_header)
        if p.exists():
            result.append(p)

    # Apply -skip-headers filtering: exclude headers whose name or path matches
    if skip_headers:
        result = [
            h
            for h in result
            if h.name not in skip_headers and str(h) not in skip_headers
        ]

    return result


def _warn_stub_flags(quiet: bool, **kwargs: object) -> None:
    """Emit warnings for P2 stub flags that were passed but have no effect."""
    for param_name, help_text in _P2_STUB_FLAGS.items():
        val = kwargs.get(param_name)
        if val is not None and val is not False and val != 0:
            _do_echo(f"Warning: {help_text}", quiet)


def _build_compat_suppression(
    skip_symbols_path: Path | None,
    skip_types_path: Path | None,
    symbols_list_path: Path | None,
    types_list_path: Path | None,
    skip_internal_symbols: str | None,
    skip_internal_types: str | None,
    suppress: Path | None,
) -> SuppressionList | None:
    """Build merged suppression rules from compat CLI sources."""
    suppression: SuppressionList | None = None
    if skip_symbols_path is not None or skip_types_path is not None:
        try:
            suppression = _build_skip_suppression(skip_symbols_path, skip_types_path)
        except (ValueError, OSError) as exc:
            _compat_fail("in skip-symbols/skip-types", exc)
    if symbols_list_path is not None or types_list_path is not None:
        try:
            suppression = _merge_suppression(
                suppression,
                _build_whitelist_suppression(symbols_list_path, types_list_path),
            )
        except (ValueError, OSError) as exc:
            _compat_fail("in symbols-list/types-list", exc)
    if skip_internal_symbols is not None or skip_internal_types is not None:
        try:
            suppression = _merge_suppression(
                suppression,
                _build_internal_suppression(skip_internal_symbols, skip_internal_types),
            )
        except ValueError as exc:
            _compat_fail("in skip-internal-symbols/skip-internal-types", exc)
    if suppress is not None:
        from ..suppression import SuppressionList  # noqa: PLC0415

        try:
            file_suppression = SuppressionList.load(suppress)
        except (ValueError, OSError) as exc:
            _compat_fail("loading suppression file", exc)
        suppression = _merge_suppression(suppression, file_suppression)
    return suppression
