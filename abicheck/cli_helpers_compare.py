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

"""Plain CLI helpers for the dump/compare/compare-release paths.

Leaf module (must not import from ``abicheck.cli``): holds the reproducible
provenance timestamp, compile-db -> castxml flag resolution, per-side
header/include resolution, dump-only flag warning, severity-config resolution,
redundant-change re-merge, additive-change collection, force-public symbol-list
merge, and the cross-release library matching helpers. These names are
re-exported from ``abicheck.cli`` to keep existing import sites (sibling
``cli_*`` modules and the test suite) working unchanged.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from .config_paths import find_config_in_dir
from .service_scan import pair_wide_cxx20_std_override
from .workflows.extraction import (
    has_explicit_std,
    strip_vendor_hash as strip_vendor_hash,
)

if TYPE_CHECKING:
    from .checker_types import Change, DiffResult
    from .compatibility_evaluation_frontend import PublicSymbolsList
    from .model import AbiSnapshot
    from .policy_file import PolicyFile
    from .service_scan import CompileContext
    from .workflows.extraction import BuildConfig
    from .workflows.gate import SeverityConfig


def _provenance_timestamp(source_date_epoch: str | None) -> str:
    """ISO-8601 UTC timestamp, honouring ``SOURCE_DATE_EPOCH`` when valid."""
    import datetime

    if source_date_epoch:
        try:
            epoch = int(source_date_epoch.strip())
            return datetime.datetime.fromtimestamp(
                epoch, tz=datetime.timezone.utc
            ).isoformat()
        except (ValueError, OverflowError, OSError):
            # Non-numeric or out-of-range epoch — fall back to wall clock
            # rather than aborting the dump.
            pass
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _resolve_build_context_flags(
    effective_compile_db: Path | None,
    headers: tuple[Path, ...],
    compile_db_filter: str | None,
) -> tuple[list[str], bool]:
    """Resolve compile database into castxml flags for dump.

    Returns ``(flags, matched)``. ``matched`` is ``True`` iff a compile-DB
    entry genuinely backs the resolved :class:`~abicheck.build_context.BuildContext`
    (its ``compile_db_path`` is set by both ``build_context_for_header``'s
    direct-match branch and ``build_context_union_fallback``'s merge branch,
    and only stays ``None`` for a syntactically valid but empty -- or entirely
    filtered-out -- compile database). Distinct from ``bool(flags)``: a
    genuinely matched TU with no ABI-relevant flags to forward (e.g. a plain
    ``cc -c src/foo.c`` with no interesting defines/includes/standard) still
    derives an empty ``flags`` list, but is real build-context evidence, not
    an absent one -- conflating the two would make ``dump lib.so -H api.h -p
    build --depth build`` wrongly reject a matched-but-flagless compile
    database (Codex review, second finding on this signal)."""
    if not effective_compile_db:
        return [], False
    from .cli_resolve import _expand_header_inputs
    from .errors import AbicheckError

    try:
        from .build_context import (
            build_context_for_header,
            build_context_union_fallback,
            load_compile_db,
        )

        db_entries = load_compile_db(effective_compile_db)
        resolved_hdrs = _expand_header_inputs(list(headers)) if headers else []
        if resolved_hdrs:
            ctx = build_context_for_header(
                db_entries,
                resolved_hdrs[0],
                source_filter=compile_db_filter,
            )
        else:
            ctx = build_context_union_fallback(
                db_entries, source_filter=compile_db_filter
            )
        flags = ctx.to_castxml_flags()
        if flags:
            click.echo(
                f"Build context: {len(db_entries)} entries from "
                f"{effective_compile_db}, {len(flags)} flags derived",
                err=True,
            )
            if ctx.has_conflicts:
                click.echo(
                    "Warning: conflicting flags detected in compile database; "
                    "using first-match values. See --verbose for details.",
                    err=True,
                )
        return flags, ctx.compile_db_path is not None
    except (AbicheckError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc


def dry_run_compile_db_matched(
    compile_db_path: Path | None,
    compile_db_path_alt: Path | None,
    headers: tuple[Path, ...],
    compile_db_filter: str | None,
) -> bool | None:
    """Silent, non-raising sibling of :func:`_resolve_build_context_flags` for
    ``dump --dry-run``'s ``--depth build`` check (external review).

    Loading a compile database and checking whether it matches the resolved
    headers is cheap, deterministic, read-only resolution -- an earlier
    version of the dry-run logic treated it as "real work out of scope for a
    dry run" and only checked bare presence, letting an empty/non-matching
    compile database dry-run as merely a soft warning even though the real
    run's strict depth gate would definitely reject it.

    Returns ``None`` when no compile database was given at all (a distinct
    case from the caller's perspective), ``True``/``False`` for the same
    verdict :func:`_resolve_build_context_flags` would compute
    (``ctx.compile_db_path is not None``) -- but never raises and never
    echoes to stderr (``_resolve_build_context_flags``'s "Build context: N
    entries..." announcement belongs to the real run, not a dry run) and
    never returns the derived flags themselves (dry-run has no use for
    them). Any load/match failure (missing file, malformed JSON, wrong
    structure) is folded into ``False`` -- the real run would fail on the
    identical input too, just via a different exception shape
    (``click.ClickException`` from a malformed compile database vs.
    ``DumpDepthNotSatisfiedError`` from an empty/unmatched one); either way
    the invocation cannot succeed, which is the only thing a dry run needs
    to report.
    """
    effective_compile_db = compile_db_path or compile_db_path_alt
    if not effective_compile_db:
        return None
    from .cli_resolve import _expand_header_inputs
    from .errors import AbicheckError

    try:
        from .build_context import (
            build_context_for_header,
            build_context_union_fallback,
            load_compile_db,
        )

        db_entries = load_compile_db(effective_compile_db)
        resolved_hdrs = _expand_header_inputs(list(headers)) if headers else []
        if resolved_hdrs:
            ctx = build_context_for_header(
                db_entries,
                resolved_hdrs[0],
                source_filter=compile_db_filter,
            )
        else:
            ctx = build_context_union_fallback(
                db_entries,
                source_filter=compile_db_filter,
            )
        return ctx.compile_db_path is not None
    except (AbicheckError, OSError, ValueError, click.ClickException):
        # click.ClickException also covers _expand_header_inputs's own
        # "header directory contains no supported header files" case -- a
        # dry run reports that as "this cannot succeed" too, not a crash.
        return False


def _merge_gcc_options(
    build_context_flags: list[str], gcc_options: str | None
) -> str | None:
    """Merge compile-db derived flags with explicit gcc options."""
    if not build_context_flags:
        return gcc_options
    merged = " ".join(build_context_flags)
    return f"{merged} {gcc_options}" if gcc_options else merged


def _resolve_per_side_options(
    headers: tuple[Path, ...],
    includes: tuple[Path, ...],
    old_headers_only: tuple[Path, ...],
    new_headers_only: tuple[Path, ...],
    old_includes_only: tuple[Path, ...],
    new_includes_only: tuple[Path, ...],
) -> tuple[list[Path], list[Path], list[Path], list[Path]]:
    """Resolve per-side headers/includes: --old-header overrides -H, etc."""
    old_h = list(old_headers_only) if old_headers_only else list(headers)
    new_h = list(new_headers_only) if new_headers_only else list(headers)
    old_inc = list(old_includes_only) if old_includes_only else list(includes)
    new_inc = list(new_includes_only) if new_includes_only else list(includes)
    return old_h, new_h, old_inc, new_inc


def _pair_wide_dialect_override(
    lang: str,
    old_h: list[Path],
    new_h: list[Path],
    compile_context: CompileContext,
    side_compile_context: CompileContext,
) -> tuple[CompileContext, CompileContext]:
    """Pin ``-std=gnu++20`` for BOTH compare sides at once, or neither (P0 fix).

    Thin wrapper around the shared core
    (:func:`~abicheck.service_scan.pair_wide_cxx20_std_override`, also used by
    ``service.run_compare_request``'s Python-API/MCP path, so the policy can't
    drift between the two front-ends) that applies the decision to this CLI
    path's two ``CompileContext`` objects: ``compile_context`` (used by the
    inline-source-embed path) and ``side_compile_context`` (used by
    ``_resolve_compare_snapshots``) — the sole call site derives the latter
    from the former via ``dataclasses.replace(compile_context, frontend="auto")``,
    so today they always carry identical ``gcc_options``/``gcc_option_tokens``;
    the explicit-std guard below checks both anyway rather than relying on
    that invariant holding for every future caller.

    An explicit ``-std=``/``--std=``/``/std:`` from the user always wins and
    this is then a no-op — auto-detection never overrides an explicit choice.
    """
    if has_explicit_std(
        compile_context.gcc_options, compile_context.gcc_option_tokens
    ) or has_explicit_std(
        side_compile_context.gcc_options, side_compile_context.gcc_option_tokens
    ):
        return compile_context, side_compile_context
    override = pair_wide_cxx20_std_override(
        lang,
        old_h,
        new_h,
        compile_context.gcc_options,
        compile_context.gcc_option_tokens,
    )
    if override is None:
        return compile_context, side_compile_context
    compile_context = dataclasses.replace(
        compile_context,
        gcc_option_tokens=(*compile_context.gcc_option_tokens, *override),
    )
    side_compile_context = dataclasses.replace(
        side_compile_context,
        gcc_option_tokens=(*side_compile_context.gcc_option_tokens, *override),
    )
    return compile_context, side_compile_context


def _warn_ignored_flags(
    old_is_binary: bool,
    new_is_binary: bool,
    headers: tuple[Path, ...],
    includes: tuple[Path, ...],
    old_headers_only: tuple[Path, ...],
    new_headers_only: tuple[Path, ...],
    old_includes_only: tuple[Path, ...],
    new_includes_only: tuple[Path, ...],
) -> None:
    """Warn if dump-only options are provided but not used (both inputs are snapshots)."""
    if old_is_binary or new_is_binary:
        return
    flag_pairs: list[tuple[tuple[Path, ...], str]] = [
        (headers, "-H/--header"),
        (old_headers_only, "--header old="),
        (new_headers_only, "--header new="),
        (includes, "-I/--include"),
        (old_includes_only, "--include old="),
        (new_includes_only, "--include new="),
    ]
    ignored_flags = [label for value, label in flag_pairs if value]
    if ignored_flags:
        click.echo(
            f"Warning: {', '.join(ignored_flags)} ignored when both inputs are snapshots.",
            err=True,
        )


#: Moved to ``compatibility_evaluation_frontend.py`` (a leaf module) so the
#: ADR-049 configuration resolver can merge the same two ``--public-symbol``/
#: ``--public-symbols-list`` sources into ``surface.explicit_scope`` without
#: importing this CLI-layer module — the resolver reading only the inline tuple
#: made a list-file-only invocation resolve to no explicit scope at all (Codex
#: review). Re-exported here, the same pattern ``_canonical_library_key`` uses.
from .compatibility_evaluation_frontend import (  # noqa: E402
    collect_force_public_symbols,
)

_collect_force_public_symbols = collect_force_public_symbols


def load_required_symbols(
    symbols: tuple[str, ...],
    symbols_file: Path | None,
) -> tuple[tuple[str, ...], tuple[str, ...], str | None]:
    """Combine ``--required-symbol`` values with a ``--required-symbols`` file.

    The file format is one symbol per line; blank lines and ``#`` comments are
    ignored (ADR-043, folds the removed ``plugin-check`` command's manifest).

    Returns the combined contract, *what the file itself contributed*, and the
    digest of its bytes (both empty/``None`` when no file was given), all from
    the one read. A required-symbol contract selects the base policy
    (ADR-043), so an ADR-049 receipt has to identify what really did it: the
    file's own contribution is what decides whether naming that file is a true
    claim, since a file that parsed to nothing selected nothing (Codex review,
    fresh evidence). The digest is over raw bytes, so it matches the file on
    disk rather than a newline-normalized rendering of it.
    """
    from_file: list[str] = []
    digest: str | None = None
    if symbols_file is not None:
        import hashlib

        data = symbols_file.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        for line in data.decode("utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                from_file.append(stripped)
    # De-duplicate while preserving first-seen order.
    return (
        tuple(dict.fromkeys([*symbols, *from_file])),
        tuple(from_file),
        digest,
    )


def resolve_force_public_scope(
    public_symbols: tuple[str, ...], symbols_list_path: str | Path | None
) -> tuple[set[str], PublicSymbolsList | None]:
    """The forced-public set, and the list file it was read from -- one read.

    ``--public-symbols-list`` feeds two consumers: the live comparison's
    forced-public overlay, and the ADR-049 receipt that names the file with
    its digest. Reading it once for both is what keeps the receipt honest --
    a second read could pair the persisted digest with content that did not
    score the run, and a file deleted mid-run would fail an otherwise
    finished comparison during receipt generation (Codex review, fresh
    evidence). Returns ``None`` for the list when no file was given, which
    is a different fact from a file that named no symbols.
    """
    from .compatibility_evaluation_frontend import PublicSymbolsList as _List

    listed = (
        _List.from_file(symbols_list_path) if symbols_list_path is not None else None
    )
    forced = collect_force_public_symbols(public_symbols, None, already_read=listed)
    return forced, listed


def _collect_additions(result: DiffResult) -> list[object]:
    """Collect additive changes in a policy-independent way."""
    from .checker_policy import COMPATIBLE_KINDS

    addition_kinds = {k for k in COMPATIBLE_KINDS if k.value.endswith("_added")}
    return [c for c in result.changes if c.kind in addition_kinds]


#: Owned by ``binary_utils.py`` (a true leaf) to break the ADR-056 cycle
#: `bundle -> cli_helpers_compare -> service -> service_scan -> bundle`; reached
#: via ``workflows.extraction`` since ADR-061 P4. Re-exported for every caller.
from .workflows.extraction import (  # noqa: E402,I001
    _canonical_library_key as _canonical_library_key,
)


#: Owned by ``binary_utils.py`` for the identical ADR-061 reason
#: ``_canonical_library_key`` is (``bundle_side_input.py``, classified
#: ``workflows``, may not import this ``frontends``-legacy module) --
#: re-exported here for back-compat, mirroring ``_canonical_library_key``'s
#: own re-export immediately above. Reached via ``workflows.extraction``,
#: not a direct ``from .binary_utils import ...``: ``frontends`` may only
#: import ``model``/``workflows``/``report`` (architecture/modules.yaml),
#: and ``binary_utils.py`` is classified ``extract``.
from .workflows.extraction import (  # noqa: E402,I001
    _version_sort_key as _version_sort_key,
)


def _collect_release_inputs(path: Path) -> list[Path]:
    """Collect compare-able inputs from a file or directory."""
    from .cli_resolve import _is_supported_compare_input

    if path.is_file():
        return [path]
    if not path.is_dir():
        raise click.ClickException(f"Input path is neither file nor directory: {path}")
    files = [p for p in sorted(path.rglob("*")) if _is_supported_compare_input(p)]
    if not files:
        raise click.ClickException(
            f"No supported ABI inputs found in directory: {path}"
        )
    return files


def _build_match_map(paths: list[Path]) -> tuple[dict[str, Path], list[str]]:
    """Build key->path map with version-aware duplicate resolution.

    The CLI-facing wrapper over :func:`abicheck.binary_utils.build_match_map`
    (the pure, Click-free primitive -- ADR-061: it lives there so
    ``bundle_side_input.py``, classified ``workflows``, can call it without a
    forbidden ``workflows -> frontends`` import): translates
    :class:`~abicheck.errors.AmbiguousLibraryMatchError` into
    ``click.ClickException`` with the identical message, so every existing
    ``compare``/``compare-release`` call site is unaffected.
    """
    from .errors import AmbiguousLibraryMatchError
    from .workflows.extraction import build_match_map

    try:
        return build_match_map(paths)
    except AmbiguousLibraryMatchError as exc:
        raise click.ClickException(str(exc)) from exc


def _resolve_severity(
    preset: str | None,
    abi_breaking: str | None,
    potential_breaking: str | None,
    quality_issues: str | None,
    addition: str | None,
) -> tuple[SeverityConfig, bool]:
    """Resolve severity configuration and return (config, explicitly_set)."""
    from .workflows.gate import resolve_severity_config

    explicitly_set = any(
        v is not None
        for v in (
            preset,
            abi_breaking,
            potential_breaking,
            quality_issues,
            addition,
        )
    )
    config = resolve_severity_config(
        preset=preset,
        abi_breaking=abi_breaking,
        potential_breaking=potential_breaking,
        quality_issues=quality_issues,
        addition=addition,
    )
    return config, explicitly_set


# ── ADR-037 D4: CLI ↔ config precedence resolver ─────────────────────────────


@dataclass(frozen=True)
class ResolvedCompareConfig:
    """The settings ``compare`` runs with, after merging CLI flags over config.

    Precedence per key is **CLI > config > built-in default** (ADR-037 D4). A
    CLI value of ``None`` means "the user did not pass the flag", so the config
    value (or the default) wins; an explicit CLI value always wins.
    """

    severity: SeverityConfig
    #: True when severity was set anywhere (CLI flag or config) — drives the
    #: ``auto`` exit-code scheme.
    severity_active: bool
    scope_public: bool
    collapse_versioned_symbols: bool
    public_symbols: tuple[str, ...]
    strict_suppressions: bool
    require_justification: bool
    #: Resolved to a concrete scheme: ``"legacy"`` or ``"severity"`` (``auto``
    #: has already been decided from ``severity_active``).
    exit_code_scheme: str
    source_method: str | None
    #: ADR-040 Lever 2: debug-resolution knobs demoted to the ``debug:`` config
    #: block (CLI flags still override). ``debug_format`` is ``None`` when unset.
    debug_format: str | None = None
    dwarf_only: bool = False
    debuginfod: bool = False
    debuginfod_url: str | None = None
    #: ADR-040 Lever 2: ``--show-redundant`` demoted to ``scope.show_redundant``.
    show_redundant: bool = False
    #: The CLI-or-config severity values (``None`` when neither set), kept raw so
    #: the directory/package fan-out can forward them to ``compare-release``
    #: without forcing severity-aware mode when nothing is configured.
    merged_severity_preset: str | None = None
    merged_severity_abi_breaking: str | None = None
    merged_severity_potential_breaking: str | None = None
    merged_severity_quality_issues: str | None = None
    merged_severity_addition: str | None = None


def resolve_compare_config(
    cfg: BuildConfig | None,
    *,
    cli_severity_preset: str | None,
    cli_scope_public: bool | None,
    cli_exit_code_scheme: str | None = None,
    cli_debug_format: str | None = None,
    cli_dwarf_only: bool | None = None,
    cli_debuginfod: bool | None = None,
    cli_debuginfod_url: str | None = None,
) -> ResolvedCompareConfig:
    """Merge CLI flags over ``.abicheck.yml`` config with built-in defaults.

    Pure (no Click/IO) so the precedence contract is unit-testable per key
    (``test_config_precedence``). Each ``cli_*`` argument is ``None`` when the
    user did not pass the corresponding flag.

    Only the keys that still have a CLI flag take a ``cli_*`` argument. The
    per-category severity levels, the suppression strict/justification pair,
    the public-symbol overlay, ``collapse_versioned_symbols`` and
    ``show_redundant`` were hidden CLI duplicates of a config key and have
    been removed from the CLI, so ``.abicheck.yml`` is now their only source
    and they are read straight off *cfg*.
    """
    from .workflows.gate import resolve_severity_config

    def _pick(cli: object, conf: object, default: object) -> object:
        if cli is not None:
            return cli
        if conf is not None:
            return conf
        return default

    # Severity: preset from CLI → config; per-category from config only.
    c_preset = cfg.severity_preset if cfg else None
    c_abi = cfg.severity_abi_breaking if cfg else None
    c_pot = cfg.severity_potential_breaking if cfg else None
    c_qual = cfg.severity_quality_issues if cfg else None
    c_add = cfg.severity_addition if cfg else None

    eff_preset = cli_severity_preset if cli_severity_preset is not None else c_preset
    eff_abi, eff_pot, eff_qual, eff_add = c_abi, c_pot, c_qual, c_add

    severity_active = any(
        v is not None for v in (eff_preset, eff_abi, eff_pot, eff_qual, eff_add)
    )
    severity = resolve_severity_config(
        preset=eff_preset,
        abi_breaking=eff_abi,
        potential_breaking=eff_pot,
        quality_issues=eff_qual,
        addition=eff_add,
    )

    scope_public = bool(
        _pick(cli_scope_public, cfg.scope_public if cfg else None, True)
    )
    collapse = bool(cfg.collapse_versioned_symbols) if cfg else False
    merged_public: list[str] = list(cfg.public_symbols) if cfg else []

    strict = bool(cfg.suppression_strict) if cfg else False
    require_just = bool(cfg.suppression_require_justification) if cfg else False

    raw_scheme = str(
        _pick(cli_exit_code_scheme, cfg.exit_code_scheme if cfg else None, "auto")
    )
    if raw_scheme == "auto":
        scheme = "severity" if severity_active else "legacy"
    else:
        scheme = raw_scheme

    source_method = cfg.source_method if cfg else None

    # ADR-040 Lever 2: debug-resolution demotion (CLI > config).
    debug_format = _pick(cli_debug_format, cfg.debug_format if cfg else None, None)
    dwarf_only = bool(
        _pick(cli_dwarf_only, cfg.debug_dwarf_only if cfg else None, False)
    )
    debuginfod = bool(
        _pick(cli_debuginfod, cfg.debug_debuginfod if cfg else None, False)
    )
    debuginfod_url = _pick(
        cli_debuginfod_url, cfg.debug_debuginfod_url if cfg else None, None
    )
    show_redundant = bool(cfg.scope_show_redundant) if cfg else False

    return ResolvedCompareConfig(
        severity=severity,
        severity_active=severity_active,
        scope_public=scope_public,
        collapse_versioned_symbols=collapse,
        public_symbols=tuple(merged_public),
        strict_suppressions=strict,
        require_justification=require_just,
        exit_code_scheme=scheme,
        source_method=source_method,
        debug_format=debug_format if isinstance(debug_format, str) else None,
        dwarf_only=dwarf_only,
        debuginfod=debuginfod,
        debuginfod_url=debuginfod_url if isinstance(debuginfod_url, str) else None,
        show_redundant=show_redundant,
        merged_severity_preset=eff_preset,
        merged_severity_abi_breaking=eff_abi,
        merged_severity_potential_breaking=eff_pot,
        merged_severity_quality_issues=eff_qual,
        merged_severity_addition=eff_add,
    )


def discover_project_config(start: Path | None = None) -> Path | None:
    """Find a project ``.abicheck.yml`` for ``compare`` (ADR-037 D4).

    Looks in *start* (default: current working directory) and then walks up to
    the filesystem root, returning the first recognized config file found —
    see :mod:`abicheck.config_paths` for the set of locations checked within
    each directory (the root spelling, ``.github/``, and
    ``.github/abicheck/``). ``compare`` runs from a project checkout, so the
    nearest enclosing config is the project's reviewed contract.
    """
    base = (start or Path.cwd()).resolve()
    for d in (base, *base.parents):
        found = find_config_in_dir(d)
        if found is not None:
            return found
    return None


def _merge_redundant_changes(result: DiffResult) -> None:
    """Re-merge redundant changes back into the main change list."""
    for c in result.changes:
        c.caused_count = 0
    for c in result.redundant_changes:
        c.caused_by_type = None
    result.changes = result.changes + result.redundant_changes
    result.redundant_changes = []
    result.redundant_count = 0


def fold_l0_hard_removals(
    old: AbiSnapshot,
    new: AbiSnapshot,
    lang: str,
    extra_changes: list[Change] | None,
) -> list[Change] | None:
    """Preserve hard ELF-only removals a header-scoped compare could hide.

    A function present in the ELF/DWARF exports can be entirely absent from
    the header AST — most commonly because it is declared behind a
    consumer-controlled macro the header pass parses without knowing the
    real build's `-D` set (``examples/case97_api_depends_on_consumer_env``:
    the header AST is parsed once per compare, with no signal for which
    macro state the *binary* was actually built under, so a macro-gated
    declaration silently drops out on both sides). When that happens the
    function never enters the header-scoped model on either side, so the
    diff has nothing to compare it against and a real ``BREAKING`` removal
    is missed.

    Delegates the actual "resolve both inputs symbols-only and diff them
    unscoped" extraction to :func:`abicheck.l0_export_delta.collect_l0_export_delta`
    (ADR-049 Phase 5 §6.3) -- the same function ``cli_scan_baseline._run_baseline_compare``
    now calls for ``scan --against`` (PR #494 originally hand-copied this
    logic in both places, locked in by ``tests/test_pr494_scan_regressions.py``;
    this function's own contribution beyond that shared core is only the
    staleness check below, since only this call site re-derives paths from
    an already-resolved snapshot that could have been read from a stale
    pre-dumped JSON file). Per ADR-028 D3 (artifact-backed evidence stays
    authoritative), this only restores a fact the ELF layer already
    asserts; it cannot manufacture a break that isn't really there.

    Re-resolves from each snapshot's own ``source_path`` — the binary it was
    actually dumped from — rather than the compare CLI's raw input paths, so
    this also covers the ``dump`` (with `-H`) *then* ``compare snap1.json
    snap2.json`` two-step workflow, not just a direct ``compare a.so b.so
    -H``: a pre-dumped JSON snapshot carries no `-H` flag of its own for
    ``compare`` to see, but it does remember the binary it came from.

    Best-effort: a raw binary input to re-resolve may not be available
    (e.g. a hand-authored JSON snapshot with no real ``source_path``, or one
    dumped on a different machine where that path no longer exists) —
    resolution failures are swallowed and *extra_changes* is returned
    unchanged.

    Identity-checked against ``source_mtime``/``source_size``: a pre-dumped
    JSON snapshot read back into ``compare snap1.json snap2.json`` records
    the mtime and byte size the binary had at dump time; if the file at
    ``source_path`` has since changed (rebuilt in place, or the path reused
    for something else) the re-probe would assert a fact about a *different*
    binary than the one the snapshots actually describe, making the compare
    non-reproducible. When either doesn't match — or either snapshot
    predates these fields — the fold-in declines rather than trust a
    possibly-stale binary. Not a cryptographic guarantee (a same-size,
    mtime-preserving rebuild — e.g. ``cp -p`` — can still slip through;
    Codex review), but a proportionate check for a best-effort enrichment
    that's already documented to swallow anything short of a clean match.

    The mtime side of that check is skipped independently for each side whose
    own ``source_mtime_epoch`` flag is set: ``dumper._safe_mtime`` recorded
    the fixed ``SOURCE_DATE_EPOCH`` value rather than that binary's real
    mtime at *dump* time (reproducible-builds spec), so a live re-probe's
    real mtime almost never equals it. Each side's flag is checked
    independently (not OR'd together) so a mixed CI/local compare — one
    snapshot dumped under a pinned epoch, the other dumped normally — still
    enforces the real mtime on the non-epoch side rather than letting one
    epoch-dumped side disable the check for both (Codex review, three
    rounds: same-process direct compares, then a dump/compare environment
    mismatch, then this per-side mix). The flag is checked per-snapshot
    rather than via the *compare*-time environment for the same reason as
    round two — a dump-time epoch must stay recognized regardless of what's
    set later. Size still applies unconditionally to both sides — it isn't
    epoch-gated and remains a real (if imperfect) identity signal.
    """
    old_path = getattr(old, "source_path", None)
    new_path = getattr(new, "source_path", None)
    if not old_path or not new_path:
        return extra_changes

    old_snapshot_mtime = getattr(old, "source_mtime", None)
    new_snapshot_mtime = getattr(new, "source_mtime", None)
    old_snapshot_size = getattr(old, "source_size", None)
    new_snapshot_size = getattr(new, "source_size", None)
    if (
        old_snapshot_mtime is None
        or new_snapshot_mtime is None
        or old_snapshot_size is None
        or new_snapshot_size is None
    ):
        return extra_changes
    try:
        old_now_stat = Path(old_path).stat()
        new_now_stat = Path(new_path).stat()
    except OSError:
        return extra_changes
    old_mtime_ok = getattr(old, "source_mtime_epoch", False) or (
        old_now_stat.st_mtime == old_snapshot_mtime
    )
    new_mtime_ok = getattr(new, "source_mtime_epoch", False) or (
        new_now_stat.st_mtime == new_snapshot_mtime
    )
    if (
        not old_mtime_ok
        or not new_mtime_ok
        or old_now_stat.st_size != old_snapshot_size
        or new_now_stat.st_size != new_snapshot_size
    ):
        return extra_changes

    from .l0_export_delta import collect_l0_export_delta

    l0_hard_removals = collect_l0_export_delta(Path(old_path), Path(new_path), lang)
    return [*(extra_changes or []), *l0_hard_removals]


# ---------------------------------------------------------------------------
# ADR-043 scoped gating (--used-by / --required-symbol(s))
#
# Relocated here from ``cli_compare_helpers`` (which sits at the AI-readiness
# 2000-line hard cap) as one self-contained family: the per-app/per-contract
# scoping passes, the runtime-probe overlay, the worst-wins exit-code and
# verdict ranking, and the JSON-safe summaries the renderer reads back off
# ``result``. A pure relocation -- ``cli_compare_helpers`` re-exports every name
# below, so ``cli_compare_helpers._verdict_exit_code`` (which
# ``cli_scan_baseline`` imports) and the existing test patch targets keep
# resolving unchanged, and a bare-name call there still goes through that
# module's namespace. This module, not a new one, because a *new* module
# reaching ``service``/``appcompat`` would join the allowlisted CLI
# import-cycle SCC, which CLAUDE.md "M1-3" forbids extending; this one is
# already a member.
# ---------------------------------------------------------------------------


def _app_compat_summary(result: object) -> dict[str, Any]:
    """Project an :class:`appcompat.AppCompatResult` into a small JSON-safe dict."""
    return {
        "app": result.app_path,  # type: ignore[attr-defined]
        "verdict": result.verdict.value,  # type: ignore[attr-defined]
        "required_symbol_count": result.required_symbol_count,  # type: ignore[attr-defined]
        "missing_symbols": result.missing_symbols,  # type: ignore[attr-defined]
        "missing_versions": result.missing_versions,  # type: ignore[attr-defined]
        "relevant_change_count": len(result.breaking_for_app),  # type: ignore[attr-defined]
        "symbol_coverage": round(result.symbol_coverage, 1),  # type: ignore[attr-defined]
    }


def _plugin_contract_summary(result: object) -> dict[str, Any]:
    """Project a :class:`appcompat.PluginHostContractResult` into a small dict."""
    return {
        "verdict": result.verdict.value,  # type: ignore[attr-defined]
        "required_entrypoints": sorted(result.required_entrypoints),  # type: ignore[attr-defined]
        "missing_entrypoints": result.missing_entrypoints,  # type: ignore[attr-defined]
        "relevant_change_count": len(result.breaking_for_host),  # type: ignore[attr-defined]
        "coverage": round(result.coverage, 1),  # type: ignore[attr-defined]
    }


def _verdict_exit_code(verdict: object) -> int:
    """Map a scoped-comparison Verdict to its floor exit code (ADR-043)."""
    value = getattr(verdict, "value", verdict)
    if value == "BREAKING":
        return 4
    if value == "API_BREAK":
        return 2
    return 0


_VERDICT_SEVERITY_RANK = {
    "BREAKING": 3,
    "API_BREAK": 2,
    "COMPATIBLE_WITH_RISK": 1,
    "COMPATIBLE": 0,
    "NO_CHANGE": 0,
}


def _verdict_severity_rank(verdict: object) -> int:
    """Rank a Verdict by severity, independent of any exit-code scheme.

    Under a severity scheme, a BREAKING app can carry exit code 0 (e.g.
    ``--severity-preset info-only``) -- ranking "worst app" by exit code
    would then let a later COMPATIBLE app (also exit code 0) overwrite the
    reported scoped verdict, so JSON/HTML/SARIF could claim COMPATIBLE while
    an earlier --used-by summary is still BREAKING (Codex review). Verdict
    selection for reporting must stay keyed on verdict severity, not on the
    (independently correct) max-exit-code computation used for gating.
    """
    value = getattr(verdict, "value", verdict)
    return _VERDICT_SEVERITY_RANK.get(value, 0) if isinstance(value, str) else 0


def _scoped_exit_code(
    scoped: Any,
    relevant_changes: list[Any],
    result: Any,
    exit_code_scheme: str,
    sev_config: Any,
    policy: str,
    policy_file: PolicyFile | None,
    *,
    has_missing_contract: bool = False,
) -> int:
    """Compute a scoped result's exit code under the active exit-code scheme.

    ADR-043's --used-by/--required-symbol(s) floor the exit code on the
    *scoped* verdict rather than the full library's -- but that floor must
    still respect ``--exit-code-scheme severity``/``--severity-preset``: without
    this, a scoped compare silently reverted to the legacy 0/2/4 mapping no
    matter what severity configuration the caller passed, because the scoped
    branch returned straight to ``sys.exit`` before the severity-aware exit
    handler ever ran.

    *has_missing_contract* (a required symbol/version/entrypoint absent from
    the new library) floors the severity-scheme exit code separately from
    *relevant_changes*: a missing contract symbol is BREAKING but is not a
    diff ``Change``, so ``compute_exit_code`` never sees it and would
    otherwise return 0 (Codex review).
    """
    if exit_code_scheme == "severity":
        from .workflows.gate import compute_exit_code, missing_contract_exit_code

        code = compute_exit_code(
            relevant_changes,
            sev_config,
            policy=policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=policy_file,
        )
        if has_missing_contract:
            code = max(code, missing_contract_exit_code(sev_config))
        return code
    return _verdict_exit_code(scoped.verdict)


def _scoped_severity_summary(
    relevant_changes: list[Any],
    missing: Iterable[str],
    result: Any,
    sev_config: Any,
    policy: str,
    policy_file: PolicyFile | None,
) -> tuple[tuple[str, ...], dict[str, int]]:
    """(blocking_categories, per-category counts) for one scoped result.

    Mirrors ``_scoped_exit_code``'s missing-contract floor: a missing
    symbol/version/entrypoint with no matching diff Change is folded into
    ``abi_breaking`` directly here -- both into the blocking-categories set
    (when abi_breaking is severity-configured as error, matching the exit
    -code floor) and into the count (always, since a count is a factual
    tally, not a gate decision) -- otherwise a missing-contract-only scoped
    BREAKING would report an empty ``blocking_categories`` alongside a
    nonzero exit code, or a ``categories.abi_breaking.count`` of 0 alongside
    a blocking ``abi_breaking`` category (Codex review). A *missing* entry
    that already has a matching Change in *relevant_changes* (e.g. a removed
    symbol is both "missing" from the new export table and a ``FUNC_REMOVED``
    Change) is excluded via ``uncovered_missing_symbols`` -- otherwise that
    single ABI break would be counted twice (Codex review follow-up).
    """
    from .appcompat import uncovered_missing_symbols
    from .workflows.gate import (
        IssueCategory,
        SeverityLevel,
        categorize_changes,
        compute_gate_decision,
    )

    categorized = categorize_changes(
        relevant_changes,
        policy=policy,
        kind_sets=result._effective_kind_sets(),
        policy_file=policy_file,
    )
    counts = {
        "abi_breaking": len(categorized.abi_breaking),
        "potential_breaking": len(categorized.potential_breaking),
        "quality_issues": len(categorized.quality_issues),
        "addition": len(categorized.addition),
    }
    gate = compute_gate_decision(
        relevant_changes,
        sev_config,
        policy=policy,
        kind_sets=result._effective_kind_sets(),
        policy_file=policy_file,
    )
    categories = list(gate.blocking_categories)
    uncovered = uncovered_missing_symbols(missing, relevant_changes)
    if uncovered:
        counts["abi_breaking"] += len(uncovered)
        if (
            sev_config.abi_breaking == SeverityLevel.ERROR
            and IssueCategory.ABI_BREAKING.value not in categories
        ):
            categories.append(IssueCategory.ABI_BREAKING.value)
    return tuple(categories), counts


def _require_used_by_binary_evidence(
    old_lib: Any,
    new_lib: Any,
    old_input: Path,
    new_input: Path,
) -> None:
    """Reject a ``--used-by`` run whose OLD/NEW side carries no binary evidence.

    A real library path always qualifies; a JSON snapshot qualifies only when it
    carries an ``elf``/``pe``/``macho`` block (i.e. it is a ``dump`` of a real
    library, not a headers-only one) -- that is what supplies the SONAME/export
    table/version list/PE ordinal table the scoping needs.
    """
    for lib, path, label in (
        (old_lib, old_input, "OLD"),
        (new_lib, new_input, "NEW"),
    ):
        has_binary_evidence = isinstance(lib, Path) or any(
            getattr(lib, field, None) is not None for field in ("elf", "pe", "macho")
        )
        if not has_binary_evidence:
            raise click.UsageError(
                f"--used-by requires OLD/NEW to be real library binaries, or "
                f"JSON snapshots carrying binary evidence (a `dump` of a real "
                f"library, not headers-only); {label} ({path}) is neither."
            )


def _apply_used_by_scoping(
    result: Any,
    used_by_apps: tuple[Path, ...],
    old_input: Path,
    new_input: Path,
    old_snapshot: Any,
    new_snapshot: Any,
    policy: str,
    policy_file: PolicyFile | None,
    exit_code_scheme: str = "legacy",
    sev_config: Any = None,
    suppression: Any = None,
) -> int:
    """Scope *result* to each ``--used-by`` app; worst-wins (ADR-043).

    OLD/NEW may be real library binaries or JSON snapshots (e.g. a saved
    ``dump`` output): a recognized binary is parsed directly; otherwise the
    already-loaded snapshot (``old_snapshot``/``new_snapshot``, from
    ``compare``'s own pipeline) is used instead, since a snapshot's
    ``elf``/``pe``/``macho`` fields already carry the SONAME/export table/
    version list/PE ordinal table :func:`~abicheck.appcompat.scope_diff_to_app`
    needs. Attaches a JSON-safe summary to ``result.used_by`` for the
    renderer and returns the worst app's exit code, computed under
    *exit_code_scheme* (legacy verdict floor, or severity-aware over each
    app's relevant changes when the caller passed a severity setting).

    *suppression* (ADR-044 P2, Codex review) is forwarded to
    :func:`~abicheck.appcompat.scope_diff_to_app`: its findings are
    synthesized *after* the pipeline's own suppression pass already ran over
    ``result.changes``, so without this they would be unsuppressible even by
    an exact rule.
    """
    from .appcompat import scope_diff_to_app
    from .service import detect_binary_format

    old_lib = old_input if detect_binary_format(old_input) is not None else old_snapshot
    new_lib = new_input if detect_binary_format(new_input) is not None else new_snapshot

    _require_used_by_binary_evidence(old_lib, new_lib, old_input, new_input)

    from .appcompat import uncovered_missing_symbols
    from .reporter import _finding_id

    summaries = []
    worst_exit = 0
    worst_verdict = None
    worst_verdict_rank = -1
    # Keyed by the change's semantic identity (kind/symbol/old/new/location/
    # description, via `_finding_id`) -- not id(change) -- so a Change or
    # missing symbol shared by two tied apps (e.g. both import the same
    # removed symbol) collapses to one entry instead of being tallied once
    # per app (Codex review) -- `_scoped_severity_summary` runs once at the
    # end over this deduplicated union, not per app summed together.
    # `id()` alone under-deduplicates PE_ORDINAL_RETARGETED findings:
    # `scope_diff_to_app` synthesizes a fresh `Change` object per app (via
    # `_check_pe_ordinal_imports`), so two apps hitting the same ordinal
    # retarget produce structurally-identical but object-distinct `Change`s
    # that `id()` would double-count in the severity summary.
    worst_changes: dict[str, Any] = {}
    worst_missing: set[str] = set()
    # Union across ALL apps (not just the worst-exit-code one) of which
    # findings this --used-by gate actually cares about -- SARIF/JUnit
    # consult this to make their own result levels/failure counts follow
    # the scoped gate instead of the full, unscoped library diff (CLI-audit
    # P1: "SARIF/JUnit computing pass/fail from the full library diff").
    relevant_finding_ids: set[str] = set()
    # Union across ALL apps of relevant Change objects, keyed by finding id --
    # not just their ids -- so scoped-only changes (e.g. PE_ORDINAL_RETARGETED,
    # which scope_diff_to_app synthesizes fresh per app and never adds to
    # result.changes) can still be rendered by SARIF/JUnit instead of only
    # contributing to the gate's exit code with nothing to explain it (Codex
    # review).
    relevant_changes_by_id: dict[str, Any] = {}
    missing_labels: set[str] = set()
    for app in used_by_apps:
        scoped = scope_diff_to_app(
            result,
            app,
            old_lib,
            new_lib,
            policy=policy,
            policy_file=policy_file,
            suppression=suppression,
            # ADR-057: old_lib above is the *path* whenever OLD is a real
            # binary, and a path carries no L5 graph -- pass the snapshot
            # compare's own pipeline already resolved so the consumer-impact
            # join can explain why a consumer required a removed symbol.
            # Graph lookup only; old_lib still owns every export/version read.
            old_snapshot=old_snapshot,
        )
        summaries.append(_app_compat_summary(scoped))
        relevant_finding_ids.update(_finding_id(c) for c in scoped.breaking_for_app)
        relevant_changes_by_id.update(
            {_finding_id(c): c for c in scoped.breaking_for_app}
        )
        # A missing symbol/version already covered by a relevant Change (e.g.
        # FUNC_REMOVED) must not also become a synthetic missing-contract
        # finding -- that would double-report the same ABI break (Codex
        # review, mirrors _scoped_severity_summary's own dedup below).
        missing_labels.update(
            uncovered_missing_symbols(
                list(scoped.missing_symbols) + list(scoped.missing_versions),
                scoped.breaking_for_app,
            )
        )
        exit_code = _scoped_exit_code(
            scoped,
            scoped.breaking_for_app,
            result,
            exit_code_scheme,
            sev_config,
            policy,
            policy_file,
            has_missing_contract=bool(
                scoped.missing_symbols or scoped.missing_versions
            ),
        )
        # exit code (gating) and verdict (reporting) are maxed/ranked
        # independently: under a severity scheme the two can disagree (a
        # BREAKING app can carry exit code 0 under e.g. `--severity-preset
        # info-only`), so picking the reported scoped_verdict by exit code
        # could let a later, less-severe app overwrite an earlier BREAKING
        # one merely because their exit codes tied at 0 (Codex review).
        if exit_code_scheme == "severity":
            if exit_code > worst_exit:
                worst_changes = {_finding_id(c): c for c in scoped.breaking_for_app}
                worst_missing = set(scoped.missing_symbols) | set(
                    scoped.missing_versions
                )
            elif exit_code == worst_exit:
                worst_changes.update(
                    {_finding_id(c): c for c in scoped.breaking_for_app}
                )
                worst_missing |= set(scoped.missing_symbols) | set(
                    scoped.missing_versions
                )
        worst_exit = max(worst_exit, exit_code)
        rank = _verdict_severity_rank(scoped.verdict)
        if worst_verdict is None or rank >= worst_verdict_rank:
            worst_verdict_rank = rank
            worst_verdict = scoped.verdict
    result.used_by = summaries  # type: ignore[attr-defined]
    result.scoped_verdict = worst_verdict  # type: ignore[attr-defined]
    result.scoped_exit_code = worst_exit  # type: ignore[attr-defined]
    result.scoped_exit_code_scheme = exit_code_scheme  # type: ignore[attr-defined]
    result.gate_scope = "used_by"  # type: ignore[attr-defined]
    result.scoped_relevant_finding_ids = frozenset(relevant_finding_ids)  # type: ignore[attr-defined]
    result.scoped_missing_labels = tuple(sorted(missing_labels))  # type: ignore[attr-defined]
    _existing_ids = {_finding_id(c) for c in result.changes}
    result.scoped_only_changes = tuple(  # type: ignore[attr-defined]
        c for fid, c in relevant_changes_by_id.items() if fid not in _existing_ids
    )
    if exit_code_scheme == "severity":
        categories, counts = _scoped_severity_summary(
            list(worst_changes.values()),
            worst_missing,
            result,
            sev_config,
            policy,
            policy_file,
        )
        result.scoped_blocking_categories = categories  # type: ignore[attr-defined]
        result.scoped_severity_counts = counts  # type: ignore[attr-defined]
    return worst_exit


def _apply_required_symbol_scoping(
    result: Any,
    required_symbols: tuple[str, ...],
    old: Any,
    new: Any,
    policy: str,
    policy_file: PolicyFile | None,
    exit_code_scheme: str = "legacy",
    sev_config: Any = None,
) -> int:
    """Scope *result* to an explicit ``--required-symbol(s)`` contract (ADR-043)."""
    from .appcompat import scope_diff_to_required_symbols, uncovered_missing_symbols
    from .reporter import _finding_id

    scoped = scope_diff_to_required_symbols(
        result,
        old,
        new,
        required_symbols,
        policy=policy,
        policy_file=policy_file,
    )
    result.required_symbols = _plugin_contract_summary(scoped)  # type: ignore[attr-defined]
    result.scoped_verdict = scoped.verdict  # type: ignore[attr-defined]
    result.gate_scope = "required_symbol"  # type: ignore[attr-defined]
    result.scoped_relevant_finding_ids = frozenset(  # type: ignore[attr-defined]
        _finding_id(c) for c in scoped.breaking_for_host
    )
    # An entrypoint already covered by a relevant Change must not also
    # become a synthetic missing-contract finding (Codex review, mirrors
    # _apply_used_by_scoping's identical dedup).
    result.scoped_missing_labels = tuple(
        sorted(  # type: ignore[attr-defined]
            uncovered_missing_symbols(
                scoped.missing_entrypoints, scoped.breaking_for_host
            )
        )
    )
    # Scoped-only changes: relevant to the host contract but never added to
    # result.changes (mirrors _apply_used_by_scoping's identical handling).
    _existing_ids = {_finding_id(c) for c in result.changes}
    result.scoped_only_changes = tuple(  # type: ignore[attr-defined]
        c for c in scoped.breaking_for_host if _finding_id(c) not in _existing_ids
    )
    exit_code = _scoped_exit_code(
        scoped,
        scoped.breaking_for_host,
        result,
        exit_code_scheme,
        sev_config,
        policy,
        policy_file,
        has_missing_contract=bool(scoped.missing_entrypoints),
    )
    result.scoped_exit_code = exit_code  # type: ignore[attr-defined]
    result.scoped_exit_code_scheme = exit_code_scheme  # type: ignore[attr-defined]
    if exit_code_scheme == "severity":
        categories, counts = _scoped_severity_summary(
            scoped.breaking_for_host,
            scoped.missing_entrypoints,
            result,
            sev_config,
            policy,
            policy_file,
        )
        result.scoped_blocking_categories = categories  # type: ignore[attr-defined]
        result.scoped_severity_counts = counts  # type: ignore[attr-defined]
    return exit_code
