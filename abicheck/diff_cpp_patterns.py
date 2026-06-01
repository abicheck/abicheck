# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Detectors for library-family-shaped ABI breaks (case77–case89).

These patterns are common in large C++ numerical/runtime libraries that
ship a co-versioned bundle of shared objects behind a single public
header tree (for example oneDAL, oneTBB, or libtorch). The detectors are
not specific to any one library — the named projects are only used as
illustrative examples.

Each detector consumes the existing change list plus the old/new
``AbiSnapshot`` and emits *new* synthetic ``Change`` entries that name a
deployment- or family-level event rather than the per-symbol primitives
that triggered it.

Implemented detectors:

* :func:`detect_serialization_tag_changes` (case81) — serialization tag
  IDs reassigned between releases.
* :func:`detect_missing_instantiations` (case79) — header advertises a
  template instantiation the shipped library no longer exports.
* :func:`detect_sycl_overload_set_removal` (case82) — bulk removal of
  ``sycl::queue&``-taking overloads.
* :func:`detect_cpu_dispatch_isa_dropped` (case83) — an ISA tier of
  dispatched specializations disappeared in one go.
* :func:`detect_tag_type_renamed` (case86) — empty tag struct rename
  with corresponding symbol-mangling shift.
* :func:`detect_default_template_arg_changed` (case87) — instantiation
  symbol re-mangled because its default template argument changed.
* :func:`detect_inline_body_renamed_member` (case89) — public inline
  accessor still references a detail:: field that was renamed.
* :func:`detect_bundle_soname_skew` (case84) — cross-artifact cohort
  invariant (operates on multiple snapshots, not a pair).
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .checker_policy import ChangeKind
from .checker_types import Change

# Re-exports — the generic detectors were extracted to dedicated modules
# in PR-D, but are re-exported here so callers that import them from this
# module keep working unchanged. (This module is internal: it is not
# exported from ``abicheck/__init__`` and has no documented public-API
# import path.)
from .diff_serialization import (  # noqa: F401
    _TAG_EXACT_LEAVES,
    _TAG_SUFFIX_PATTERNS,
    _collect_tag_constants,
    _looks_like_serialization_tag,
    detect_serialization_tag_changes,
)
from .diff_templates import (  # noqa: F401
    _looks_like_template_instantiation,
    detect_missing_instantiations,
)
from .diff_templates import (
    _strip_template_args as _callable_stem,
)

if TYPE_CHECKING:
    from .model import AbiSnapshot, Function, RecordType

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _is_empty_record(t: object) -> bool:
    """An empty tag struct: no fields, no vtable, size 0 or 1 byte."""
    fields = getattr(t, "fields", None) or []
    vtable = getattr(t, "vtable", None) or []
    size_bits = getattr(t, "size_bits", None)
    if fields or vtable:
        return False
    # Empty C++ classes are 1 byte by [class.size]; ``None`` is accepted
    # because some parsers omit the size for empty types.
    return size_bits in (None, 0, 8)


def _last_segment(qualified_name: str) -> str:
    """Return the last ``::``-separated segment of *qualified_name*."""
    if "::" in qualified_name:
        return qualified_name.rsplit("::", 1)[1]
    return qualified_name


def _parent_namespace(qualified_name: str) -> str:
    if "::" in qualified_name:
        return qualified_name.rsplit("::", 1)[0]
    return ""


# ---------------------------------------------------------------------------
# case81 — serialization tag ID reassigned
# (detector moved to abicheck.diff_serialization in PR-D; re-exported at top)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# case79 — missing template instantiation
# (detector moved to abicheck.diff_templates in PR-D; re-exported at top)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# case82 — SYCL overload set removed
# ---------------------------------------------------------------------------

_SYCL_QUEUE_PARAM_RE = re.compile(r"\bsycl\s*::\s*queue\b")


def _has_sycl_queue_first_param(fn: Function) -> bool:
    if not fn.params:
        return False
    first = fn.params[0]
    return bool(_SYCL_QUEUE_PARAM_RE.search(first.type or ""))


def _unqualified_function_name(name: str) -> str:
    """Return the unqualified function name from a (possibly qualified)
    demangled name, dropping any template args on either the function
    itself or its enclosing class.

    Examples:

    * ``ns::function``                       → ``function``
    * ``ns::function<int>``                  → ``function``
    * ``ns::Class<float, A>::method``        → ``method``
    * ``ns::Class<X>::method<Y>``            → ``method``
    """
    # Drop template-arg groups at top level so the trailing ``::xxx`` is
    # not chopped off by a naive ``split("<")``.
    depth = 0
    out: list[str] = []
    for ch in name:
        if ch == "<":
            depth += 1
            continue
        if ch == ">":
            depth -= 1
            continue
        if depth == 0:
            out.append(ch)
    cleaned = "".join(out)
    return _last_segment(cleaned)


def detect_sycl_overload_set_removal(
    old: AbiSnapshot,
    new: AbiSnapshot,
    *,
    min_overloads: int = 2,
) -> tuple[list[Change], set[str]]:
    """Detect bulk removal of overloads that take ``sycl::queue&``.

    Returns ``(findings, suppressed_mangled_set)``. The second element
    lists the per-symbol mangled names that should be filtered out of
    the ``func_removed`` stream because they are children of the grouped
    finding.
    """
    old.index()
    new.index()
    new_mangled = {f.mangled for f in new.functions}
    # Group removed SYCL-overload candidates by the *qualified* callable
    # stem (full namespace path with template args stripped). Keying by
    # the unqualified leaf name alone would let unrelated symbols like
    # ``ns1::foo::compute(sycl::queue&)`` and ``ns2::bar::compute()`` cross-
    # match — a false ``SYCL_OVERLOAD_SET_REMOVED`` that hides the real
    # per-symbol removals. Same fix that ``detect_default_template_arg_changed``
    # already uses.
    by_entity: dict[str, list[Function]] = defaultdict(list)
    for fn in old.functions:
        if fn.mangled in new_mangled:
            continue
        if not _has_sycl_queue_first_param(fn):
            continue
        by_entity[_callable_stem(fn.name)].append(fn)
    # Surviving non-SYCL siblings give us confidence that the family
    # was withdrawn deliberately (DPC++ build disabled), not that the
    # whole algorithm was deleted. Use the same qualified key.
    surviving_non_sycl: set[str] = set()
    for fn in new.functions:
        if not _has_sycl_queue_first_param(fn):
            surviving_non_sycl.add(_callable_stem(fn.name))
    findings: list[Change] = []
    suppressed: set[str] = set()
    affected_unq: list[str] = []
    affected_mangled: list[str] = []
    for entity, removed in by_entity.items():
        if entity not in surviving_non_sycl:
            continue
        for fn in removed:
            affected_mangled.append(fn.mangled)
            suppressed.add(fn.mangled)
            # See `detect_cpu_dispatch_isa_dropped` for the rationale —
            # platforms differ on whether `Change.symbol` is the
            # castxml-derived mangled name or a sibling export-name form.
            if fn.name:
                suppressed.add(fn.name)
        # The report shows the unqualified leaf so it reads as
        # "compute, train, infer" rather than full namespaced paths.
        affected_unq.append(_last_segment(entity) or entity)
    if len(affected_unq) >= min_overloads:
        affected_unq.sort()
        findings.append(
            Change(
                kind=ChangeKind.SYCL_OVERLOAD_SET_REMOVED,
                symbol="<sycl_overload_family>",
                description=(
                    f"SYCL overload family withdrawn: {len(affected_mangled)} "
                    f"overloads taking ``sycl::queue&`` were removed across "
                    f"{len(affected_unq)} entry points "
                    f"({', '.join(affected_unq[:10])}"
                    f"{'…' if len(affected_unq) > 10 else ''}). "
                    f"This is the deployment-level event 'DPC++ build "
                    f"disabled' rather than independent API removals — "
                    f"consumers built against the SYCL surface need a "
                    f"DPC++-enabled rebuild."
                ),
                affected_symbols=affected_mangled,
            )
        )
    else:
        suppressed.clear()
    return findings, suppressed


# ---------------------------------------------------------------------------
# case83 — CPU dispatch ISA dropped
# ---------------------------------------------------------------------------

# Ordered most-specific to least-specific so that ``avx512`` wins over
# ``avx`` and ``sse42`` over ``sse``.
_ISA_TOKENS: tuple[str, ...] = (
    "avx512",
    "avx2",
    "avx",
    "sse42",
    "sse41",
    "sse2",
    "sse",
    "neon",
    "sve",
    "scalar",
    "generic",
)


def _isa_token_in_symbol(symbol_name: str) -> str | None:
    """Find the most specific ISA token in *symbol_name*.

    Looks for ``_<token>_`` or trailing ``_<token>``. Case-insensitive.
    Returns the canonical lowercase token or ``None``.
    """
    if not symbol_name:
        return None
    lowered = symbol_name.lower()
    for token in _ISA_TOKENS:
        if f"_{token}_" in lowered or lowered.endswith(f"_{token}"):
            return token
    return None


def _isa_strip_token(symbol_name: str, token: str) -> str:
    """Remove the ISA token from *symbol_name* to get the algorithm stem."""
    lowered = symbol_name
    # Replace `_token_` first, then trailing `_token`.
    lowered = re.sub(
        rf"_{re.escape(token)}(?=(_|$))",
        "",
        lowered,
        flags=re.IGNORECASE,
    )
    return lowered


def _build_removed_by_isa(old_functions: Iterable[Function], new_mangled: set[str]) -> dict[str, list[tuple[str, str]]]:
    """Build a mapping from ISA token → list of (stem, mangled) for functions removed in new."""
    removed_by_isa: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for fn in old_functions:
        if fn.mangled in new_mangled:
            continue
        token = _isa_token_in_symbol(fn.name) or _isa_token_in_symbol(fn.mangled)
        if token is None:
            continue
        stem = _isa_strip_token(fn.name, token)
        removed_by_isa[token].append((stem, fn.mangled))
    return removed_by_isa


def _build_all_surviving_stems(new_functions: Iterable[Function]) -> set[str]:
    """Return the union of algorithm stems that still exist under any ISA in the new snapshot."""
    surviving_stems_by_isa: dict[str, set[str]] = defaultdict(set)
    for fn in new_functions:
        token = _isa_token_in_symbol(fn.name) or _isa_token_in_symbol(fn.mangled)
        if token is None:
            continue
        surviving_stems_by_isa[token].add(_isa_strip_token(fn.name, token))
    return set().union(*surviving_stems_by_isa.values()) if surviving_stems_by_isa else set()


def _suppress_demangled_names(
    overlapping: list[tuple[str, str]],
    old_index: dict[str, Function],
    suppressed: set[str],
) -> None:
    """Add demangled function names to *suppressed* for cross-platform robustness.

    Belt-and-suspenders: Different platforms emit ``func_removed`` ``Change.symbol``
    with different conventions. Adding the demangled name catches the case where
    castxml-derived ``fn.mangled`` and PE-export-derived ``Change.symbol`` are
    sibling forms of the same underlying name.
    """
    for _, mangled in overlapping:
        removed_fn = old_index.get(mangled)
        if removed_fn is not None and removed_fn.name:
            suppressed.add(removed_fn.name)


def _emit_isa_dropped_finding(
    token: str,
    overlapping: list[tuple[str, str]],
    old_index: dict[str, Function],
    suppressed: set[str],
) -> Change:
    """Build the CPU_DISPATCH_ISA_DROPPED Change and update *suppressed* in place."""
    affected_stems = {stem for stem, _ in overlapping}
    affected_mangled = [m for _, m in overlapping]
    suppressed.update(affected_mangled)
    _suppress_demangled_names(overlapping, old_index, suppressed)
    stems_sorted = sorted(affected_stems)
    return Change(
        kind=ChangeKind.CPU_DISPATCH_ISA_DROPPED,
        symbol=f"<isa:{token}>",
        description=(
            f"CPU dispatch ISA '{token}' tier removed: "
            f"{len(affected_mangled)} specialisations across "
            f"{len(affected_stems)} algorithms "
            f"({', '.join(stems_sorted[:8])}"
            f"{'…' if len(stems_sorted) > 8 else ''}). "
            f"Runtime dispatcher continues to work; consumers that "
            f"pinned directly to '{token}' symbols get unresolved "
            f"references at load time."
        ),
        affected_symbols=affected_mangled,
    )


def detect_cpu_dispatch_isa_dropped(
    old: AbiSnapshot,
    new: AbiSnapshot,
    *,
    min_removed: int = 3,
) -> tuple[list[Change], set[str]]:
    """Detect mass removal of one CPU ISA's dispatched specializations.

    Returns ``(findings, suppressed_mangled_set)``. Suppressed symbols
    are those rolled up under the grouped finding so the per-symbol
    ``func_removed`` noise doesn't double-count.
    """
    old.index()
    new.index()
    new_mangled = {f.mangled for f in new.functions}
    removed_by_isa = _build_removed_by_isa(old.functions, new_mangled)
    all_surviving_stems = _build_all_surviving_stems(new.functions)
    old_index: dict[str, Function] = {f.mangled: f for f in old.functions}
    findings: list[Change] = []
    suppressed: set[str] = set()
    for token, removed in removed_by_isa.items():
        if len(removed) < min_removed:
            continue
        # Only group symbols whose algorithm stem still survives under some
        # other ISA. Fully-removed algorithms keep their per-symbol
        # ``func_removed`` finding so the user sees the real deletion.
        overlapping = [(stem, mangled) for stem, mangled in removed if stem in all_surviving_stems]
        if len(overlapping) < min_removed:
            continue
        findings.append(_emit_isa_dropped_finding(token, overlapping, old_index, suppressed))
    return findings, suppressed


# ---------------------------------------------------------------------------
# case86 — tag type renamed (empty struct rename)
# ---------------------------------------------------------------------------


def _symbols_embedding_leaf(mangled_set: set[str], leaf: str) -> list[str]:
    """Return mangled names from *mangled_set* that embed *leaf* (with or without underscores)."""
    token = leaf.replace("_", "")
    return [m for m in mangled_set if leaf in m or token in m]


def _find_tag_rename_for_removed(
    removed: RecordType,
    candidates: list[RecordType],
    only_removed: set[str],
    only_added: set[str],
) -> Change | None:
    """Try to match *removed* to one of the *candidates* added types via symbol evidence.

    Returns a TAG_TYPE_RENAMED Change on the first matching candidate, or None.
    """
    old_leaf = _last_segment(removed.name)
    removed_with_token = _symbols_embedding_leaf(only_removed, old_leaf)
    if not removed_with_token:
        return None
    for added in candidates:
        new_leaf = _last_segment(added.name)
        added_with_token = _symbols_embedding_leaf(only_added, new_leaf)
        if not added_with_token:
            continue
        return Change(
            kind=ChangeKind.TAG_TYPE_RENAMED,
            symbol=removed.name,
            description=(
                f"Empty tag struct '{removed.name}' renamed to "
                f"'{added.name}'. The type has no fields or vtable, so "
                f"layout-based detectors see no change, but "
                f"{len(removed_with_token)} explicit instantiation "
                f"symbol(s) referencing the old name were re-mangled "
                f"(now {len(added_with_token)} symbol(s) reference the "
                f"new name). Consumers built against the old header "
                f"fail to resolve the instantiation at load time."
            ),
            old_value=removed.name,
            new_value=added.name,
            affected_symbols=removed_with_token,
        )
    return None


def _empty_records_only_in(types_a: dict[str, RecordType], types_b: dict[str, RecordType]) -> list[RecordType]:
    """Return records that are empty and present in *types_a* but not *types_b*."""
    return [t for name, t in types_a.items() if name not in types_b and _is_empty_record(t)]


def _group_by_namespace(types: list[RecordType]) -> dict[str, list[RecordType]]:
    """Index a list of type objects by their parent namespace."""
    by_ns: dict[str, list[RecordType]] = defaultdict(list)
    for t in types:
        by_ns[_parent_namespace(t.name)].append(t)
    return by_ns


def detect_tag_type_renamed(
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> list[Change]:
    """Detect rename of an empty tag struct used in template specialisations.

    Heuristic: an empty record vanishes from *old* and an empty record
    appears in *new* under the same parent namespace, AND there is at
    least one removed symbol whose mangled name embeds the old tag's
    leaf segment while at least one added symbol embeds the new leaf.
    """
    old.index()
    new.index()
    old_types = {t.name: t for t in old.types}
    new_types = {t.name: t for t in new.types}
    removed_empties = _empty_records_only_in(old_types, new_types)
    added_empties = _empty_records_only_in(new_types, old_types)
    if not removed_empties or not added_empties:
        return []
    added_by_ns = _group_by_namespace(added_empties)
    only_removed = {f.mangled for f in old.functions} - {f.mangled for f in new.functions}
    only_added = {f.mangled for f in new.functions} - {f.mangled for f in old.functions}
    findings: list[Change] = []
    for removed in removed_empties:
        ns = _parent_namespace(removed.name)
        candidates = added_by_ns.get(ns, [])
        if not candidates:
            continue
        ch = _find_tag_rename_for_removed(removed, candidates, only_removed, only_added)
        if ch is not None:
            findings.append(ch)
    return findings


# ---------------------------------------------------------------------------
# case87 — default template argument changed
# ---------------------------------------------------------------------------


def _stable_leading_template_args(old_args: str, new_args: str) -> bool:
    """Return ``True`` iff *old_args* and *new_args* differ ONLY in
    trailing positions — i.e. some non-empty leading prefix is identical.

    Used to gate ``DEFAULT_TEMPLATE_ARG_CHANGED`` so we don't flag a
    pairing like ``Foo<float, A>::compute`` → ``Foo<double, B>::compute``
    where every position changed (that's an explicit instantiation
    change, not a default-arg change). For a default-arg change, C++
    requires defaults to live in trailing positions, so the leading
    args supplied by the user must remain identical between old and
    new substituted forms.

    Single-arg templates are accepted as a degenerate case (no leading
    args to compare; the only arg IS the trailing position).

    >>> _stable_leading_template_args("float, A", "float, B")
    True
    >>> _stable_leading_template_args("float, A", "double, B")
    False
    >>> _stable_leading_template_args("A", "B")  # 1-arg degenerate case
    True
    """
    old_list = [p.strip() for p in _split_top_level_commas_local(old_args)]
    new_list = [p.strip() for p in _split_top_level_commas_local(new_args)]
    if not old_list or not new_list:
        return False
    # Degenerate single-arg template: nothing leading to compare.
    if len(old_list) == 1 and len(new_list) == 1:
        return True
    # Same length, identical leading prefix of size >= 1.
    if len(old_list) != len(new_list):
        return False
    diff_idx = next(
        (i for i in range(len(old_list)) if old_list[i] != new_list[i]),
        len(old_list),
    )
    return diff_idx >= 1 and diff_idx < len(old_list)


def _split_top_level_commas_local(s: str) -> list[str]:
    """Split *s* on commas not nested inside ``<...>``. Lightweight local
    copy of the helper in ``internal_leak`` to avoid a cross-module
    dependency for a small parser.
    """
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in s:
        if ch == "<":
            depth += 1
            buf.append(ch)
        elif ch == ">":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


def _extract_template_args(demangled: str) -> str | None:
    """Return the substring inside the outermost (balanced) ``<...>`` of
    *demangled*.

    Handles all common shapes:

    * ``ns::function<float>``                       → ``float``
    * ``ns::function<float>(int)``                  → ``float``
    * ``ns::Class<float, A>::method``               → ``float, A``
    * ``Outer<X<int>>::Inner<Y>``                   → ``Y`` (innermost class)
    """
    head = demangled.split("(", 1)[0]
    # Walk right-to-left to find the rightmost balanced ``<...>`` group;
    # we want the template args of the leaf entity (method or function).
    last_open = -1
    depth = 0
    end = -1
    for i in range(len(head) - 1, -1, -1):
        ch = head[i]
        if ch == ">":
            if depth == 0:
                end = i
            depth += 1
        elif ch == "<":
            depth -= 1
            if depth == 0:
                last_open = i
                break
    if last_open < 0 or end < 0:
        return None
    return head[last_open + 1 : end]


def detect_default_template_arg_changed(
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> list[Change]:
    """Pair a removed instantiation with an added instantiation that
    differ only in their default-substituted template arguments.

    Heuristic: same unqualified function name, both demangled names
    show template args ``<...>``, and the args differ. Without a fully
    parsed template signature this can produce false positives if the
    user changed an instantiation explicitly — to control that, we only
    emit the finding when the args differ but the *prefix* (everything
    up to the differing arg) matches and the function unqualified name
    matches one-for-one.
    """
    old.index()
    new.index()
    new_mangled = {f.mangled for f in new.functions}
    removed = [f for f in old.functions if f.mangled not in new_mangled]
    # Key by *qualified* callable stem (full namespace path with all
    # template args stripped). This prevents matching ``ns1::foo::compute``
    # against ``ns2::bar::compute`` and other namespace-confusion false
    # positives — only different instantiations of the SAME callable get
    # paired.
    added_by_entity: dict[str, list[Function]] = defaultdict(list)
    for fn in new.functions:
        added_by_entity[_callable_stem(fn.name)].append(fn)
    findings: list[Change] = []
    seen_pairs: set[tuple[str, str]] = set()
    for fn in removed:
        old_args = _extract_template_args(fn.name)
        if old_args is None:
            continue
        entity = _callable_stem(fn.name)
        for cand in added_by_entity.get(entity, []):
            new_args = _extract_template_args(cand.name)
            if new_args is None or new_args == old_args:
                continue
            # Require that the two argument lists differ only in trailing
            # positions — i.e. there is a non-empty leading prefix that
            # matches. A "default template argument changed" can only
            # affect positions the user didn't supply explicitly, which
            # are by C++ rule the trailing ones; an explicit change to a
            # LEADING arg is something else (and should not be flagged
            # here, since it's not subsumed by a default change).
            if not _stable_leading_template_args(old_args, new_args):
                continue
            key = (fn.mangled, cand.mangled)
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            findings.append(
                Change(
                    kind=ChangeKind.DEFAULT_TEMPLATE_ARG_CHANGED,
                    symbol=fn.mangled,
                    description=(
                        f"Template instantiation '{fn.name}' substitutes to "
                        f"different arguments than its surviving sibling "
                        f"'{cand.name}'. This is consistent with a change to a "
                        f"default template argument in the declaring header: "
                        f"consumer source compiles unchanged, but the "
                        f"substituted mangled symbol differs. Consumers built "
                        f"against the old default get unresolved symbols."
                    ),
                    old_value=old_args,
                    new_value=new_args,
                )
            )
            break  # one pairing per removed symbol
    return findings


# ---------------------------------------------------------------------------
# case89 — inline accessor references renamed pimpl member
# ---------------------------------------------------------------------------


def _collect_field_rename_candidates(
    changes: Iterable[Change],
    namespaces: tuple[str, ...],
) -> list[tuple[str, str, str]]:
    """Return ``(record_name, old_field, new_field)`` triples from FIELD_RENAMED changes
    whose record belongs to an internal namespace."""
    from .internal_leak import is_internal_type  # local import: cycle-free

    candidates: list[tuple[str, str, str]] = []
    for ch in changes:
        if ch.kind != ChangeKind.FIELD_RENAMED:
            continue
        record_name = ch.symbol.rsplit("::", 1)[0] if "::" in ch.symbol else ""
        if not is_internal_type(record_name, namespaces):
            continue
        if ch.old_value and ch.new_value:
            candidates.append((record_name, str(ch.old_value), str(ch.new_value)))
    return candidates


def _collect_paired_field_candidates(
    changes: Iterable[Change],
    namespaces: tuple[str, ...],
) -> list[tuple[str, str, str]]:
    """Synthesise ``(record_name, old_field, new_field)`` triples from paired
    TYPE_FIELD_REMOVED / TYPE_FIELD_ADDED changes on the same internal type.

    Covers the case where the AST emitter doesn't produce a FIELD_RENAMED
    but does produce paired field deltas (a "modernize naming" refactor).
    """
    from .internal_leak import is_internal_type  # local import: cycle-free

    by_internal: dict[str, tuple[list[str], list[str]]] = defaultdict(lambda: ([], []))
    for ch in changes:
        if ch.kind not in (ChangeKind.TYPE_FIELD_REMOVED, ChangeKind.TYPE_FIELD_ADDED):
            continue
        # `symbol` for these is typically "Type::field_name"
        if "::" not in ch.symbol:
            continue
        rec, fld = ch.symbol.rsplit("::", 1)
        if not is_internal_type(rec, namespaces):
            continue
        removed_list, added_list = by_internal[rec]
        if ch.kind == ChangeKind.TYPE_FIELD_REMOVED:
            removed_list.append(fld)
        else:
            added_list.append(fld)

    candidates: list[tuple[str, str, str]] = []
    for rec, (removed, added) in by_internal.items():
        # Pair positionally — equal count is the strongest hint of a rename batch.
        if removed and added and len(removed) == len(added):
            for old_field, new_field in zip(removed, added, strict=False):
                candidates.append((rec, old_field, new_field))
    return candidates


def _emit_inline_body_findings(
    rename_candidates: list[tuple[str, str, str]],
    old_types: Mapping[str, object],
    new_types: Mapping[str, object],
    old_functions: Iterable[Function],
    namespaces: tuple[str, ...],
) -> list[Change]:
    """For each rename candidate emit INLINE_BODY_REFERENCES_RENAMED_MEMBER findings.

    Looks for a public class whose fields include a pimpl pointing at the
    internal type AND at least one inline public function declared on that class.
    """
    findings: list[Change] = []
    seen: set[tuple[str, str, str]] = set()
    for internal_type, old_field, new_field in rename_candidates:
        public_holders = _find_public_pimpl_holders(new_types.values(), internal_type, namespaces)
        if not public_holders:
            public_holders = _find_public_pimpl_holders(old_types.values(), internal_type, namespaces)
        if not public_holders:
            continue
        inline_funcs = _inline_accessors_for(old_functions, public_holders)
        if not inline_funcs:
            continue
        for holder in sorted(public_holders):
            key = (holder, internal_type, old_field)
            if key in seen:
                continue
            seen.add(key)
            findings.append(Change(
                kind=ChangeKind.INLINE_BODY_REFERENCES_RENAMED_MEMBER,
                symbol=holder,
                description=(
                    f"Public class '{holder}' has inline accessors "
                    f"({len(inline_funcs)} found) reaching into "
                    f"'{internal_type}' by name. Field '{old_field}' was "
                    f"renamed to '{new_field}' in the new internal layout. "
                    f"Consumers compiled against the old header have the "
                    f"old member name baked into their inline accessor "
                    f"bodies; running against the new library reads the "
                    f"wrong offset or fails to resolve the member."
                ),
                old_value=old_field,
                new_value=new_field,
            ))
    return findings


def detect_inline_body_renamed_member(
    old: AbiSnapshot,
    new: AbiSnapshot,
    changes: Iterable[Change],
    namespaces: tuple[str, ...] = ("detail", "impl", "internal"),
) -> list[Change]:
    """Detect an inline public accessor whose body references a
    member that was renamed inside an internal-namespace type.

    Heuristic: any ``field_renamed`` (or removed+added field pair) on a
    record type whose name segment matches an internal namespace,
    combined with at least one inline public function present in *both*
    snapshots whose enclosing type contains a pimpl pointing at the
    internal type.

    The detector does not attempt to parse the inline body — it cannot
    see the source. It relies on the structural signal: the offending
    member belongs to a detail:: type and there exist public inline
    accessors on the containing class.
    """
    # Materialise the iterable once so both collection passes can use it.
    changes_list = list(changes)

    # Index types by name.
    old_types = {t.name: t for t in old.types}
    new_types = {t.name: t for t in new.types}

    # Gather rename candidates from two complementary signals.
    rename_candidates = _collect_field_rename_candidates(changes_list, namespaces)
    rename_candidates.extend(_collect_paired_field_candidates(changes_list, namespaces))

    if not rename_candidates:
        return []

    return _emit_inline_body_findings(rename_candidates, old_types, new_types, old.functions, namespaces)


def _find_public_pimpl_holders(
    types: Iterable[object],
    internal_type_name: str,
    namespaces: tuple[str, ...],
) -> set[str]:
    """Return names of *public* record types that hold a pimpl pointing
    at *internal_type_name*."""
    from .internal_leak import is_internal_type

    found: set[str] = set()
    leaf = _last_segment(internal_type_name)
    for t in types:
        name = getattr(t, "name", "")
        if is_internal_type(name, namespaces):
            continue
        fields = getattr(t, "fields", None) or []
        for fld in fields:
            ftype = getattr(fld, "type", "") or ""
            if internal_type_name in ftype or leaf in ftype:
                found.add(name)
                break
    return found


def _inline_accessors_for(
    functions: Iterable[Function],
    holders: set[str],
) -> list[Function]:
    """Return inline public functions whose qualified name lives inside
    one of *holders*."""
    out: list[Function] = []
    for fn in functions:
        if not getattr(fn, "is_inline", False):
            continue
        # qualified function name like "ns::Holder::method_name"
        if "::" not in fn.name:
            continue
        holder = fn.name.rsplit("::", 1)[0]
        if holder in holders:
            out.append(fn)
    return out


# ---------------------------------------------------------------------------
# case84 — bundle SONAME skew
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BundleMember:
    """A single library participating in a bundle SONAME-skew check."""

    library: str  # filename, e.g. "libfoo_core.so.2"
    soname: str  # DT_SONAME, e.g. "libfoo_core.so.2"
    soname_major: int  # extracted major, e.g. 2


def _extract_soname_major(soname: str) -> int | None:
    """Extract the trailing major from a SONAME like
    ``libfoo_core.so.2`` or ``libfoo.2.dylib``. Returns ``None`` if
    no integer suffix can be found."""
    if not soname:
        return None
    m = re.search(r"\.so\.(\d+)$", soname)
    if m:
        return int(m.group(1))
    m = re.search(r"\.(\d+)\.dylib$", soname)
    if m:
        return int(m.group(1))
    m = re.search(r"-(\d+)\.dll$", soname)
    if m:
        return int(m.group(1))
    return None


def _cohort_key(library: str) -> str:
    """Strip version-y suffixes to derive a cohort key for clustering."""
    name = library
    # Drop everything from the first dot onwards: libfoo_core.so.2
    # -> libfoo_core.
    return name.split(".", 1)[0]


def detect_bundle_soname_skew(
    old_members: list[BundleMember],
    new_members: list[BundleMember],
    *,
    cohort_prefix: str | None = None,
) -> list[Change]:
    """Emit ``BUNDLE_SONAME_SKEW`` when some libraries in a cohort
    bumped major SONAME between *old_members* and *new_members* but
    others did not.

    *cohort_prefix*, if provided, restricts the analysis to libraries
    whose cohort key starts with this prefix (e.g. ``"libfoo_"``).
    """
    # Map new libraries by their cohort key to allow the old library
    # filename (which carries the old major) to look up the new entry.
    new_by_cohort: dict[str, BundleMember] = {}
    for m in new_members:
        new_by_cohort[_cohort_key(m.library)] = m
    # Compute (cohort_key -> delta) where delta = new_major - old_major
    deltas: dict[str, tuple[BundleMember, BundleMember, int]] = {}
    for m in old_members:
        ckey = _cohort_key(m.library)
        if cohort_prefix and not ckey.startswith(cohort_prefix):
            continue
        new_member = new_by_cohort.get(ckey)
        if new_member is None:
            continue
        old_maj = m.soname_major
        new_maj = new_member.soname_major
        deltas[ckey] = (m, new_member, new_maj - old_maj)
    if not deltas:
        return []
    bumped = [k for k, (_, _, d) in deltas.items() if d > 0]
    stayed = [k for k, (_, _, d) in deltas.items() if d == 0]
    if not bumped or not stayed:
        return []
    bumped_list = sorted(
        f"{deltas[k][0].library} → {deltas[k][1].library}" for k in bumped
    )
    stayed_list = sorted(deltas[k][1].library for k in stayed)
    desc = (
        f"Bundle SONAME skew: {len(bumped)} of {len(deltas)} cohort "
        f"members bumped major SONAME but {len(stayed)} did not. "
        f"Bumped: {', '.join(bumped_list[:5])}"
        f"{'…' if len(bumped_list) > 5 else ''}. "
        f"Lagging: {', '.join(stayed_list[:5])}"
        f"{'…' if len(stayed_list) > 5 else ''}. "
        f"Distro packages built on this set carry inconsistent dependency "
        f"metadata; mixed loads can corrupt internal cross-library state."
    )
    return [
        Change(
            kind=ChangeKind.BUNDLE_SONAME_SKEW,
            symbol="<bundle>",
            description=desc,
            old_value=str(sorted(deltas[k][0].library for k in stayed + bumped)),
            new_value=str(sorted(deltas[k][1].library for k in stayed + bumped)),
            affected_symbols=stayed_list,
        )
    ]


def bundle_members_from_directory(directory: str) -> list[BundleMember]:
    """Convenience: scan *directory* for ELF/Mach-O/PE shared libraries
    and return :class:`BundleMember` entries.

    Uses ``abicheck.binary_utils`` to read SONAME / install-name / dll
    name. Only callable when the directory exists; designed for use
    from CLI integrations (e.g. ``compare-release``).
    """
    members: list[BundleMember] = []
    if not os.path.isdir(directory):
        return members
    for name in sorted(os.listdir(directory)):
        full = os.path.join(directory, name)
        if not os.path.isfile(full):
            continue
        soname = _read_soname_best_effort(full)
        if not soname:
            continue
        major = _extract_soname_major(soname)
        if major is None:
            continue
        members.append(
            BundleMember(
                library=name,
                soname=soname,
                soname_major=major,
            )
        )
    return members


def _read_soname_best_effort(path: str) -> str | None:
    """Read DT_SONAME (ELF) / LC_ID_DYLIB (Mach-O). Best-effort: returns
    ``None`` if the file isn't a recognised shared library."""
    try:
        with open(path, "rb") as fh:
            magic = fh.read(4)
    except OSError:
        return None
    if magic == b"\x7fELF":
        return _read_elf_soname(path)
    # Mach-O / PE support deferred — current case84 example is Linux-only.
    return None


def _read_elf_soname(path: str) -> str | None:
    """Minimal ELF DT_SONAME reader using ``abicheck.elf_metadata`` when
    available; falls back to ``None``."""
    try:
        from pathlib import Path

        from .elf_metadata import parse_elf_metadata
    except ImportError:
        return None
    try:
        meta = parse_elf_metadata(Path(path))
    except Exception:  # noqa: BLE001 — defensive: tolerate any parse error
        return None
    return meta.soname or None if meta is not None else None
