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
import shlex
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
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


def _fold_gcc_option_tokens(
    gcc_options: str | None, tokens: tuple[str, ...]
) -> str | None:
    """Fold repeatable ``--gcc-option`` tokens into the ``--gcc-options`` string.

    Each ``--gcc-option`` value is one literal compiler argument that must reach
    castxml intact, including arguments that contain whitespace (e.g. a macro
    value or a path with a space). ``--gcc-options`` is later split with
    ``shlex.split``, so each token is ``shlex.quote``-escaped here to round-trip
    back to a single argument rather than shattering on whitespace.
    """
    if not tokens:
        return gcc_options
    quoted = " ".join(shlex.quote(t) for t in tokens)
    return f"{gcc_options} {quoted}" if gcc_options else quoted


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
