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

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from .buildsource.inline import BuildConfig
    from .checker_types import DiffResult
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
) -> list[str]:
    """Resolve compile database into castxml flags for dump."""
    if not effective_compile_db:
        return []
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
        return flags
    except (AbicheckError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc


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
        (old_headers_only, "--old-header"),
        (new_headers_only, "--new-header"),
        (includes, "-I/--include"),
        (old_includes_only, "--old-include"),
        (new_includes_only, "--new-include"),
    ]
    ignored_flags = [label for value, label in flag_pairs if value]
    if ignored_flags:
        click.echo(
            f"Warning: {', '.join(ignored_flags)} ignored when both inputs are snapshots.",
            err=True,
        )


def _collect_force_public_symbols(
    public_symbols: tuple[str, ...],
    symbols_list: Path | None,
) -> set[str]:
    """Merge --public-symbol values with a --public-symbols-list file.

    The list file is one symbol per line; blank lines and ``#`` comments are
    ignored (à la abi-compliance-checker -symbols-list). Inline trailing
    comments are not stripped — a ``#`` must start the line to be a comment.
    """
    out: set[str] = {s.strip() for s in public_symbols if s.strip()}
    if symbols_list is not None:
        for raw in symbols_list.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#"):
                out.add(line)
    return out


def _collect_additions(result: DiffResult) -> list[object]:
    """Collect additive changes in a policy-independent way."""
    from .checker_policy import COMPATIBLE_KINDS

    addition_kinds = {k for k in COMPATIBLE_KINDS if k.value.endswith("_added")}
    return [c for c in result.changes if c.kind in addition_kinds]


def _canonical_library_key(path: Path) -> str:
    """Canonical key used to match libraries across releases.

    For ELF versioned names, canonicalize to ``*.so`` (e.g. ``libfoo.so.1.2`` → ``libfoo.so``).
    """
    lower = path.name.lower()
    m = re.search(r"\.so(?:\.|$)", lower)
    if m:
        return lower[: m.start() + 3]
    return lower


def _version_sort_key(
    path: Path, canonical_key: str
) -> tuple[list[tuple[int, int | str]], str]:
    """Build a version-aware sort key for ambiguous library candidates."""
    lower = path.name.lower()
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
