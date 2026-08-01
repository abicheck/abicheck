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
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import click

from ._compiler_options import has_explicit_std
from .binary_utils import strip_vendor_hash as strip_vendor_hash
from .service_scan import pair_wide_cxx20_std_override

if TYPE_CHECKING:
    from .buildsource.inline import BuildConfig
    from .checker_types import Change, DiffResult
    from .compatibility_evaluation_frontend import PublicSymbolsList
    from .model import AbiSnapshot
    from .service_scan import CompileContext
    from .severity import SeverityConfig


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
                db_entries, resolved_hdrs[0], source_filter=compile_db_filter,
            )
        else:
            ctx = build_context_union_fallback(
                db_entries, source_filter=compile_db_filter,
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
#: importing this CLI-layer module — the resolver reading only the inline
#: tuple made a list-file-only invocation resolve to no explicit scope at all
#: (Codex review). Re-exported here for every existing caller, the same
#: pattern ``_canonical_library_key`` already follows below.
from .compatibility_evaluation_frontend import (  # noqa: E402
    collect_force_public_symbols,
)

_collect_force_public_symbols = collect_force_public_symbols


def load_required_symbols(
    symbols: tuple[str, ...], symbols_file: Path | None,
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


#: Moved to ``binary_utils.py`` (a true leaf module) to break an import
#: cycle (`bundle -> cli_helpers_compare -> service -> service_scan ->
#: bundle`, ADR-056) introduced when ``service_scan.run_scan_set`` started
#: importing ``bundle.discover_artifact_set``, which itself needs this
#: function. Re-exported here for every existing caller of
#: ``cli_helpers_compare._canonical_library_key`` (notably ``cli.py``).
from .binary_utils import _canonical_library_key as _canonical_library_key  # noqa: E402


def _version_sort_key(
    path: Path, canonical_key: str
) -> tuple[list[tuple[int, int | str]], str]:
    """Build a version-aware sort key for ambiguous library candidates.

    Uses the vendor-hash-stripped name (G9) so an auditwheel/delocate content
    hash never enters the comparison — otherwise the hash's digits/letters can
    outrank the real SONAME version tokens and ``_build_match_map`` picks a
    stale duplicate over the newer one (Codex review, PR #551).
    """
    lower = strip_vendor_hash(path.name.lower())
    remainder = lower
    if canonical_key.endswith(".so") and canonical_key in lower:
        remainder = lower[lower.find(canonical_key) + len(canonical_key) :]
    # strip known wrapper extensions for snapshots/dumps
    for suffix in (".json", ".pl", ".pm"):
        if remainder.endswith(suffix):
            remainder = remainder[: -len(suffix)]
            break
    remainder = remainder.lstrip("._-")
    tokens = re.findall(r"\d+|[a-z]+", remainder)
    parsed: list[tuple[int, int | str]] = []
    for tok in tokens:
        if tok.isdigit():
            parsed.append((1, int(tok)))
        else:
            parsed.append((0, tok))
    return parsed, lower


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
    """Build key->path map with version-aware duplicate resolution."""
    buckets: dict[str, list[Path]] = {}
    for p in paths:
        buckets.setdefault(_canonical_library_key(p), []).append(p)

    mapping: dict[str, Path] = {}
    warnings: list[str] = []
    for key, vals in buckets.items():
        ordered = sorted(vals, key=lambda x: _version_sort_key(x, key))
        selected = ordered[-1]
        mapping[key] = selected
        if len(ordered) > 1:
            warnings.append(
                f"Ambiguous match for '{key}': {[v.name for v in ordered]}; using '{selected.name}'"
            )
    return mapping, warnings


def _resolve_severity(
    preset: str | None,
    abi_breaking: str | None,
    potential_breaking: str | None,
    quality_issues: str | None,
    addition: str | None,
) -> tuple[SeverityConfig, bool]:
    """Resolve severity configuration and return (config, explicitly_set)."""
    from .severity import resolve_severity_config

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
    cli_severity_abi_breaking: str | None,
    cli_severity_potential_breaking: str | None,
    cli_severity_quality_issues: str | None,
    cli_severity_addition: str | None,
    cli_scope_public: bool | None,
    cli_collapse_versioned_symbols: bool | None,
    cli_public_symbols: tuple[str, ...] = (),
    cli_strict_suppressions: bool | None = None,
    cli_require_justification: bool | None = None,
    cli_exit_code_scheme: str | None = None,
    cli_debug_format: str | None = None,
    cli_dwarf_only: bool | None = None,
    cli_debuginfod: bool | None = None,
    cli_debuginfod_url: str | None = None,
    cli_show_redundant: bool | None = None,
) -> ResolvedCompareConfig:
    """Merge CLI flags over ``.abicheck.yml`` config with built-in defaults.

    Pure (no Click/IO) so the precedence contract is unit-testable per key
    (``test_config_precedence``). Each ``cli_*`` argument is ``None`` when the
    user did not pass the corresponding flag.
    """
    from .severity import resolve_severity_config

    def _pick(cli: object, conf: object, default: object) -> object:
        if cli is not None:
            return cli
        if conf is not None:
            return conf
        return default

    # Severity: merge preset + per-category from CLI → config → preset/default.
    c_preset = cfg.severity_preset if cfg else None
    c_abi = cfg.severity_abi_breaking if cfg else None
    c_pot = cfg.severity_potential_breaking if cfg else None
    c_qual = cfg.severity_quality_issues if cfg else None
    c_add = cfg.severity_addition if cfg else None

    eff_preset = cli_severity_preset if cli_severity_preset is not None else c_preset
    eff_abi = cli_severity_abi_breaking if cli_severity_abi_breaking is not None else c_abi
    eff_pot = (
        cli_severity_potential_breaking
        if cli_severity_potential_breaking is not None
        else c_pot
    )
    eff_qual = (
        cli_severity_quality_issues if cli_severity_quality_issues is not None else c_qual
    )
    eff_add = cli_severity_addition if cli_severity_addition is not None else c_add

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
    collapse = bool(
        _pick(
            cli_collapse_versioned_symbols,
            cfg.collapse_versioned_symbols if cfg else None,
            False,
        )
    )
    # Public-symbol overlay is additive: config list + any CLI additions.
    merged_public: list[str] = list(cfg.public_symbols) if cfg else []
    for s in cli_public_symbols:
        if s not in merged_public:
            merged_public.append(s)

    strict = bool(
        _pick(cli_strict_suppressions, cfg.suppression_strict if cfg else None, False)
    )
    require_just = bool(
        _pick(
            cli_require_justification,
            cfg.suppression_require_justification if cfg else None,
            False,
        )
    )

    raw_scheme = str(
        _pick(cli_exit_code_scheme, cfg.exit_code_scheme if cfg else None, "auto")
    )
    if raw_scheme == "auto":
        scheme = "severity" if severity_active else "legacy"
    else:
        scheme = raw_scheme

    source_method = cfg.source_method if cfg else None

    # ADR-040 Lever 2: debug-resolution + show-redundant demotion (CLI > config).
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
    show_redundant = bool(
        _pick(cli_show_redundant, cfg.scope_show_redundant if cfg else None, False)
    )

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
    the filesystem root, returning the first ``.abicheck.yml`` found. ``compare``
    runs from a project checkout, so the nearest enclosing config is the
    project's reviewed contract.
    """
    base = (start or Path.cwd()).resolve()
    for d in (base, *base.parents):
        candidate = d / ".abicheck.yml"
        if candidate.is_file():
            return candidate
    return None


def _merge_redundant_changes(result: DiffResult) -> None:
    """Re-merge redundant changes back into the main change list."""
    for c in result.changes:
        if c.caused_count > 0:
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
