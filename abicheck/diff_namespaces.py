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

"""Namespace-shape pattern detectors (header-only / template library follow-up).

These detectors handle ABI/API events that are best described at the
*namespace* level rather than the symbol level. They are generic — they
apply to any C++ library that uses experimental namespaces or std
re-exports — and are not tied to a particular library.

Detectors emitted here:

* ``EXPERIMENTAL_GRADUATED`` — a name in ``experimental::`` (or
  ``preview::``, ``v0::``) is now also present at a stable name in the
  new headers while the experimental alias is kept.

* ``EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT`` — a name in
  ``experimental::`` was removed and no declaration with the same leaf
  name exists at a stable location in the new headers.

* ``STD_REEXPORT_REMOVED`` — a public function whose declaration is just
  a ``using std::X;`` re-export was deleted. Detection works on
  qualified declared names alone, no DWARF body required.

All three are deliberately *source-level* findings; they fire whether or
not the underlying mangled symbol disappears, because the consumer
break is at compile time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from .checker_policy import ChangeKind, ReachabilityState
from .checker_types import Change
from .diff_helpers import make_change
from .diff_templates import _strip_param_signature
from .qualified_name_segments import (
    segments as _segments,
    version_strip_segments as _version_strip_segments,
    version_suffix as _version_suffix,  # noqa: F401  (public-surface re-export)
)

if TYPE_CHECKING:
    from .model import AbiSnapshot, RecordType, ScopeOrigin

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

# Namespace segments that mark a declaration as "not yet promised stable".
# Matched as a whole segment between ``::``; substring matches inside
# identifiers like ``ExperimentalView`` are intentionally not flagged.
DEFAULT_EXPERIMENTAL_NAMESPACES: tuple[str, ...] = (
    "experimental",
    "preview",
    "v0",
)


def _strip_experimental(
    qualified: str,
    experimental_namespaces: tuple[str, ...] = DEFAULT_EXPERIMENTAL_NAMESPACES,
) -> tuple[str, str | None]:
    """Return ``(stable_name, matched_segment)``.

    If any segment of ``qualified`` is an experimental namespace, that
    single segment is removed and the rest is rejoined. The first
    matching segment is returned so callers can name it in the
    description. When no experimental segment is present, returns
    ``(qualified, None)`` unchanged.

    Removes only the first matched segment to keep the transformation
    invertible for nested ``experimental::ranges::`` cases — callers can
    re-run the helper to peel additional layers if needed.
    """
    segs = _segments(qualified)
    for i, s in enumerate(segs):
        if s in experimental_namespaces:
            return "::".join(segs[:i] + segs[i + 1 :]), s
    return qualified, None


def _qualified_function_name(
    name: str, mangled: str, demangled: dict[str, str] | None = None
) -> str:
    """Return the best-effort qualified declaration name for a function.

    Header-derived snapshots populate ``Function.name`` with the
    qualified declaration name (``acme::lib::sort``). ELF-only mode
    leaves ``Function.name`` set to the mangled string; in that case we
    fall back to demangling of the mangled name.

    When iterating all functions of a snapshot, pass a *demangled* map
    (from :func:`_batch_demangle_public`) so the whole snapshot is demangled in
    a single batched ``c++filt`` call instead of one subprocess per symbol —
    the per-symbol path is what makes namespace detection explode on large
    stripped libraries. The lazy single-symbol fallback is kept for callers
    that have no batch (and is itself memoised in ``demangle_batch``).

    A demangled string is a *full declaration* — return type, qualified
    name, parameter list, and trailing qualifiers (``ns::C::f(ns::T
    const&, long) const``) — not merely a qualified name. This is
    deliberately returned as-is, signature included: it is what
    distinguishes one overload from another for callers that index
    functions by qualified name (:func:`_func_index_items`) — a
    caller that needs only the *leaf* member name must strip the signature
    itself (via :func:`diff_templates._strip_param_signature`) before
    segmenting, rather than have it stripped here, or two overloads
    (``f(int)``, ``f(double)``) collapse onto one identity and an overload
    that is removed while a sibling survives goes unreported (Codex
    review).
    """
    if "::" in name or "<" in name:
        return name
    if mangled.startswith("_Z"):
        if demangled is not None:
            return demangled.get(mangled, name)
        from .demangle import demangle_batch

        return demangle_batch([mangled]).get(mangled, name)
    return name


# ---------------------------------------------------------------------------
# Detector: experimental → stable graduation / removal
# ---------------------------------------------------------------------------


def _split_experimental(
    qnames: list[str],
    experimental_namespaces: tuple[str, ...],
) -> tuple[list[str], list[str]]:
    """Split *qnames* into ``(experimental, stable)`` by namespace match."""
    exp = [q for q in qnames if any(s in experimental_namespaces for s in _segments(q))]
    stable = [q for q in qnames if q not in exp]
    return exp, stable


class _IndexItem(NamedTuple):
    """One indexable declaration: its qualified name, experimental-stripped
    path, reported leaf, and alias-identity evidence (``None`` if none)."""

    qname: str
    stripped: str
    leaf: str
    identity: object | None


def _paired_stable_indices(
    old_items: list[_IndexItem],
    new_items: list[_IndexItem],
) -> tuple[dict[tuple[str, str], list[str]], dict[tuple[str, str], list[str]]]:
    """Build the OLD and NEW ``(stripped_name, leaf) -> [qname, ...]``
    indices *jointly*, so a genuine alias pair resolves to the SAME output
    key on both sides -- merging two spellings that differ only by a
    versioned inline-namespace segment (``v1``, ``_V2``, ``__1``, ...) but
    ONLY when *identity* proves they name the same declaration.

    A versioned *inline* namespace makes one declaration reachable under
    two qualified spellings: the full path (``ns::v1::x``) and the
    version-elided path (``ns::x``) that unqualified lookup from the
    enclosing scope also resolves to. But a version-shaped segment name is
    not proof of an *inline* namespace -- ``v1`` is a legal name for an
    ordinary namespace too, in which case ``ns::v1::x`` and ``ns::x`` are
    two unrelated declarations that happen to share a leaf name (Codex
    review, P1: collapsing them on name shape alone can both hide a
    genuine value/removal on one spelling and misreport the other). Each
    item's ``identity`` is a value from the snapshot's own extraction data
    that is invariant across an inline-namespace's two spellings but not
    expected to coincide for two unrelated declarations (currently only a
    function's mangled name qualifies -- see ``_type_index_items`` for two
    candidate type identities that were tried and falsified) -- ``None``
    when no identity evidence is available. Two items are only unioned into the
    same component when their identity values are both non-``None`` and
    equal; an item with no evidence, or with evidence that never coincides
    with anything else's, stays its own singleton component -- the
    pre-existing double-report is the accepted fallback rather than a
    newly-introduced false suppression.

    Deciding this *jointly* over the pooled OLD+NEW items (not building
    each snapshot's index independently, as an earlier version of this
    function did) closes two distinct false positives Codex review found
    in the independent version: (1) key instability -- a merged
    component's chosen output key depended on encounter order, so the same
    alias pair listed in a different declaration order between old and new
    could resolve to two different keys and read as a spurious removal;
    (2) membership asymmetry -- when only ONE side actually has both
    spellings coexisting (an extractor starting or stopping the duplicate
    emission), the singleton side kept its raw, un-canonicalized key while
    the multi-spelling side's key was canonicalized, so the two sides
    disagreed on the key for what is still the very same entity. Pooling
    both sides' items before computing connected components and choosing
    ``min(component)`` as every member's output key (deterministic given
    the component's *membership*, itself already order-independent since
    every pair is checked via identity equality regardless of encounter
    order) fixes both: the same component, and therefore the same key, is
    computed once and reused for whichever side(s) actually contain each
    member.
    """
    pooled: list[tuple[str, _IndexItem]] = [
        *(("old", it) for it in old_items),
        *(("new", it) for it in new_items),
    ]
    canon_groups: dict[tuple[str, str], list[tuple[str, _IndexItem]]] = {}
    for side, item in pooled:
        canon_segs, _ = _version_strip_segments(_segments(item.stripped))
        canon_groups.setdefault(("::".join(canon_segs), item.leaf), []).append(
            (side, item)
        )

    old_out: dict[tuple[str, str], list[str]] = {}
    new_out: dict[tuple[str, str], list[str]] = {}

    def _emit(side: str, key: tuple[str, str], qname: str) -> None:
        (old_out if side == "old" else new_out).setdefault(key, []).append(qname)

    for entries in canon_groups.values():
        if len(entries) == 1:
            side, item = entries[0]
            _emit(side, (item.stripped, item.leaf), item.qname)
            continue
        # Union-find over this (typically 2-4 entry) pooled group, unioning
        # any two entries whose identity values are equal and non-None.
        parent = list(range(len(entries)))

        def _find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for i in range(len(entries)):
            ident_i = entries[i][1].identity
            if ident_i is None:
                continue
            for j in range(i + 1, len(entries)):
                ident_j = entries[j][1].identity
                if ident_j is not None and ident_i == ident_j:
                    ri, rj = _find(i), _find(j)
                    if ri != rj:
                        parent[rj] = ri
        components: dict[int, list[int]] = {}
        for i in range(len(entries)):
            components.setdefault(_find(i), []).append(i)
        for members in components.values():
            key = (
                min(entries[i][1].stripped for i in members),
                entries[members[0]][1].leaf,
            )
            for i in members:
                side, item = entries[i]
                _emit(side, key, item.qname)
    return old_out, new_out


def _func_index_items(
    snap: AbiSnapshot,
    experimental_namespaces: tuple[str, ...],
) -> list[_IndexItem]:
    """Collect public functions as ``_IndexItem``\\ s for the paired index.

    Only public functions are indexed so internal helpers in
    ``experimental::`` don't get reported.
    """
    from .model import Visibility

    demangled = _batch_demangle_public(snap)
    out: list[_IndexItem] = []
    for f in snap.functions:
        if f.visibility != Visibility.PUBLIC:
            continue
        # qname keeps its parameter list (when demangled) — that's what
        # distinguishes one overload from another for the (stripped, leaf)
        # index key below; collapsing it here would let two overloads
        # (`f(int)`, `f(double)`) share one identity, so removing just one
        # of them while the other survives would go unreported (Codex
        # review). Only the *leaf* — the reported member name — needs the
        # signature stripped, since `_segments()` doesn't track `(`/`)`
        # depth and would otherwise split inside a namespace-qualified
        # parameter type.
        qname = _qualified_function_name(f.name, f.mangled, demangled)
        leaf_segs = _segments(_strip_param_signature(qname))
        if not leaf_segs:
            continue
        leaf = leaf_segs[-1]
        stripped, _ = _strip_experimental(qname, experimental_namespaces)
        # A function's mangled name is real extraction-data identity (the
        # Itanium/MSVC ABI mangles an inline namespace's segment either
        # way, so a true alias's two declared spellings still share one
        # mangled symbol) -- unlike guessing aliasing from name shape. An
        # empty mangled name is not identity evidence -- two declarations
        # both missing a mangled name would otherwise spuriously "match"
        # each other (CodeRabbit review).
        out.append(_IndexItem(qname, stripped, leaf, f.mangled or None))
    return out


def _type_index_items(
    snap: AbiSnapshot,
    experimental_namespaces: tuple[str, ...],
) -> list[_IndexItem]:
    """Collect types as ``_IndexItem``\\ s for the paired index.

    Identity is always ``None`` here -- unlike a function's mangled name,
    no field on ``RecordType`` has survived adversarial review as reliable
    alias-identity evidence, so two spellings of a type are never merged;
    the pre-existing double-report is the accepted, documented limitation
    for types (mirrored below for constants). Two attempts were tried and
    falsified, both by concrete Codex review counterexamples:

    1. A structural (kind/size/alignment/fields/bases) fingerprint --
       shown to coincide routinely between genuinely unrelated
       declarations: trivially for two empty tag/marker types sharing a
       kind, and non-trivially for two types that merely happen to share
       a field layout (a common shape for simple POD-like types) --
       either way letting an unrelated survivor silently absorb a real
       removal on the versioned spelling.
    2. The type's declaring ``source_location`` (``"header.h:42"``) --
       plausible since two spellings of one physical AST declaration
       resolve to the same node and would share it by construction, the
       way a function's mangled name does. Falsified: `dumper_clang.py`'s
       and `dwarf_snapshot.py`'s own `_source_location`/`_resolve_decl_file`
       docstrings both state a location can legitimately be a *bare
       filename*, with no line, when the line is unavailable (clang: a
       declaration on the same source line as its parent; DWARF: no
       `DW_AT_decl_line`) -- so two unrelated types in the same file both
       missing line info collide on an identical bare-filename location.
       `file:line` itself isn't even guaranteed unique across declarations
       written or macro-expanded on one physical line.

    Closing this for real needs identity ``RecordType`` doesn't carry
    today (a stable per-declaration id from the AST/DWARF backend) -- out
    of scope here, same as the analogous gap for constants.
    """
    out: list[_IndexItem] = []
    for t in snap.types:
        qname = t.name
        segs = _segments(qname)
        if not segs:
            continue
        leaf = segs[-1]
        stripped, _ = _strip_experimental(qname, experimental_namespaces)
        out.append(_IndexItem(qname, stripped, leaf, None))
    return out


def _origin_by_name(types: list[RecordType]) -> dict[str, ScopeOrigin]:
    """Map qualified type name -> ``ScopeOrigin``, first occurrence wins.

    Mirrors ``pattern_verdicts._exact_record``'s exact-identity lookup (which
    takes the first match for a given name via a linear scan) rather than a
    plain ``{t.name: t.origin for t in types}`` dict comprehension, which
    would silently let a later duplicate-named entry overwrite an earlier
    one. Two ``RecordType`` entries sharing one exact qualified name in a
    single snapshot is unusual input, but staying consistent with the
    established "first occurrence is authoritative" convention costs
    nothing here.
    """
    out: dict[str, ScopeOrigin] = {}
    for t in types:
        out.setdefault(t.name, t.origin)
    return out


def _classify_experimental_event(
    old_exp: list[str],
    old_stable: list[str],
    new_exp: list[str],
    new_stable: list[str],
) -> str | None:
    """Return ``"graduated"``, ``"removed"``, or ``None`` for a key pair.

    Graduation requires an experimental presence in old AND a new stable
    twin that did not exist before. Removal requires no replacement on
    either side. Everything else is silent.
    """
    if not old_exp:
        return None
    if new_exp and new_stable and not old_stable:
        return "graduated"
    if not new_exp and not new_stable and not old_stable:
        return "removed"
    return None


def _emit_experimental_change(
    event: str,
    leaf: str,
    old_exp: list[str],
    new_stable: list[str],
    kind_label: str,
    *,
    old_origins: dict[str, ScopeOrigin] | None,
    new_origins: dict[str, ScopeOrigin] | None,
) -> Change:
    """Build the ``Change`` record for one classified event.

    ADR-044 D1 (Codex review): the function-sourced path (``kind_label ==
    "declaration"``, ``old_origins``/``new_origins`` both ``None``) is only
    ever built from ``_func_index_items``, which indexes public
    functions only — so unlike the reverted "any non-internal-namespaced
    subject" heuristic (which had to guess at a raw symbol's visibility with
    no reliable signal), a function finding's mere existence already proves
    its subject is public, and is tagged directly at construction time,
    mirroring ``internal_leak._build_leak_change``/``diff_templates._leak_change``
    — these run via ``DetectNamespacePatterns``, *after* ``MarkReachability``,
    so nothing else would ever tag them.

    The type-sourced path (``kind_label == "type"``, origins dicts provided)
    has no ``Visibility`` field to fall back on — ``RecordType`` carries
    none (unlike ``Function``/``Variable``) — but it *does* carry ``origin``
    (ADR-024's ``ScopeOrigin``, opt-in via ``--public-header``/
    ``--public-header-dir``): a type explicitly scoped to the public-header
    set (``ScopeOrigin.PUBLIC_HEADER``) is a reliable public-reachability
    signal Codex review pointed out was overlooked. Without that opt-in flag
    every type's origin is ``ScopeOrigin.UNKNOWN``, so this degrades to the
    prior untagged behavior automatically for the common case.
    """
    from .model import ScopeOrigin

    old_q = old_exp[0]
    if event == "graduated":
        new_q = new_stable[0]
        subject_is_public = (
            new_origins is None or new_origins.get(new_q) == ScopeOrigin.PUBLIC_HEADER
        )
        return make_change(
            ChangeKind.EXPERIMENTAL_GRADUATED,
            symbol=new_q,
            detail=kind_label,
            old=old_q,
            new=new_q,
            public_reachable=subject_is_public,
            reachability_state=(
                ReachabilityState.PROVEN_REACHABLE
                if subject_is_public
                else ReachabilityState.UNKNOWN
            ),
            reachability_kind="direct_public_symbol" if subject_is_public else None,
        )
    subject_is_public = (
        old_origins is None or old_origins.get(old_q) == ScopeOrigin.PUBLIC_HEADER
    )
    return make_change(
        ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT,
        symbol=old_q,
        name=leaf,
        detail=kind_label,
        old=old_q,
        new_value=None,
        public_reachable=subject_is_public,
        reachability_state=(
            ReachabilityState.PROVEN_REACHABLE
            if subject_is_public
            else ReachabilityState.UNKNOWN
        ),
        reachability_kind="direct_public_symbol" if subject_is_public else None,
    )


def _findings_for(
    old_index: dict[tuple[str, str], list[str]],
    new_index: dict[tuple[str, str], list[str]],
    experimental_namespaces: tuple[str, ...],
    kind_label: str,
    *,
    old_origins: dict[str, ScopeOrigin] | None = None,
    new_origins: dict[str, ScopeOrigin] | None = None,
) -> list[Change]:
    """Walk old/new indices, emitting one finding per classified event.

    *old_origins*/*new_origins* are ``None`` for the function-sourced path
    (always reliably public) or a ``{qualified_name: ScopeOrigin}`` map for
    the type-sourced path (public only when ``ScopeOrigin.PUBLIC_HEADER``).
    """
    out: list[Change] = []
    for (stable_key, leaf), qnames in old_index.items():
        old_exp, old_stable = _split_experimental(qnames, experimental_namespaces)
        if not old_exp:
            continue
        new_qnames = new_index.get((stable_key, leaf), [])
        new_exp, new_stable = _split_experimental(
            new_qnames,
            experimental_namespaces,
        )
        event = _classify_experimental_event(
            old_exp,
            old_stable,
            new_exp,
            new_stable,
        )
        if event is None:
            continue
        out.append(
            _emit_experimental_change(
                event,
                leaf,
                old_exp,
                new_stable,
                kind_label,
                old_origins=old_origins,
                new_origins=new_origins,
            )
        )
    return out


def detect_experimental_namespace_changes(
    old: AbiSnapshot,
    new: AbiSnapshot,
    experimental_namespaces: tuple[str, ...] = DEFAULT_EXPERIMENTAL_NAMESPACES,
) -> list[Change]:
    """Report graduations and silent removals from experimental namespaces.

    For every public declaration in ``old`` whose qualified name contains
    an experimental segment, look up the corresponding ``leaf``-named
    declaration in ``new``:

    * If the experimental name is still present *and* a stable-namespace
      twin now exists → ``EXPERIMENTAL_GRADUATED`` (compatible).
    * If the experimental name is gone and no stable twin exists →
      ``EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT`` (API break).

    Functions and types are handled independently; a graduated *type*
    and graduated *function* with the same leaf are reported as two
    separate findings (they really are two separate API events).

    No finding is emitted when the experimental name is gone but a
    stable twin exists *and* the stable twin already existed in
    ``old`` — that's just deletion of a redundant alias, not a
    graduation event.
    """
    out: list[Change] = []
    old_func_index, new_func_index = _paired_stable_indices(
        _func_index_items(old, experimental_namespaces),
        _func_index_items(new, experimental_namespaces),
    )
    out.extend(
        _findings_for(
            old_func_index,
            new_func_index,
            experimental_namespaces,
            "declaration",
        )
    )
    old_type_index, new_type_index = _paired_stable_indices(
        _type_index_items(old, experimental_namespaces),
        _type_index_items(new, experimental_namespaces),
    )
    out.extend(
        _findings_for(
            old_type_index,
            new_type_index,
            experimental_namespaces,
            "type",
            old_origins=_origin_by_name(old.types),
            new_origins=_origin_by_name(new.types),
        )
    )
    return out


# ---------------------------------------------------------------------------
# Detector: std re-export removed
# ---------------------------------------------------------------------------

# Heuristic: a function whose declared qualified name resolves to a
# library namespace AND whose mangled name resolves to a name in
# ``std::`` is a re-export (the library names it via ``using std::X``).
#
# Concrete forms we accept:
#   - Function.name == "lib::ns::par"         (declared in library headers)
#   - Function.mangled demangles to a name beginning with "std::"
#     (the underlying definition belongs to the standard library).
#
# We DO NOT use libstdc++/libc++ internal-namespace heuristics here —
# false positives on real library functions would be worse than missing
# the occasional re-export. The detector therefore requires both halves
# of the signal to fire.

_STD_PREFIX = "std::"


def _looks_like_std_reexport(
    declared_qualified: str,
    underlying_qualified: str,
) -> bool:
    """Return True when declared_qualified is a non-std alias for underlying_qualified.

    Both names must be fully qualified. The underlying name must live in
    ``std::``; the declared name must live somewhere else (any library
    namespace). Identical names — i.e. the function genuinely lives in
    ``std::`` — are not re-exports.
    """
    if not declared_qualified or not underlying_qualified:
        return False
    declared_segs = _segments(declared_qualified)
    underlying_segs = _segments(underlying_qualified)
    if not declared_segs or not underlying_segs:
        return False
    # Declared must NOT be in std::; underlying MUST be in std::.
    if declared_segs[0] == "std":
        return False
    if underlying_segs[0] != "std":
        return False
    # Same leaf name on both sides — a using-declaration preserves the leaf.
    return declared_segs[-1] == underlying_segs[-1]


def _collect_public_declared_names(snap: AbiSnapshot) -> set[str]:
    """Return the set of qualified declared names of public functions in *snap*."""
    from .model import Visibility

    demangled = _batch_demangle_public(snap)
    out: set[str] = set()
    for f in snap.functions:
        if f.visibility != Visibility.PUBLIC:
            continue
        qname = _qualified_function_name(f.name, f.mangled, demangled)
        if qname:
            out.add(qname)
    return out


def _batch_demangle_public(snap: AbiSnapshot) -> dict[str, str]:
    """Demangle every public mangled name in *snap* in one batch call."""
    from .demangle import demangle_batch
    from .model import Visibility

    mangled = [
        f.mangled
        for f in snap.functions
        if f.mangled.startswith("_Z") and f.visibility == Visibility.PUBLIC
    ]
    return demangle_batch(mangled) if mangled else {}


def _build_std_reexport_change(declared: str, underlying: str) -> Change:
    """Build a single ``STD_REEXPORT_REMOVED`` finding.

    ADR-044 D1 (Codex review): only ever emitted for a declaration that was a
    *public* function (``detect_std_reexport_removed`` filters on
    ``Visibility.PUBLIC`` before calling this) — same construction-time
    tagging rationale as ``_emit_experimental_change``.
    """
    return make_change(
        ChangeKind.STD_REEXPORT_REMOVED,
        symbol=declared,
        name=declared,
        detail=underlying,
        old_value=f"{declared} → {underlying}",
        new_value=None,
        public_reachable=True,
        reachability_state=ReachabilityState.PROVEN_REACHABLE,
        reachability_kind="direct_public_symbol",
    )


def detect_std_reexport_removed(
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> list[Change]:
    """Report ``using std::X;`` re-exports that disappeared from public headers.

    A re-export is detected when the OLD snapshot has a public function
    whose declared qualified name lives in a library namespace but whose
    mangled name demangles to ``std::``. If the same declared qualified
    name is absent from the NEW snapshot's function set, we emit one
    ``STD_REEXPORT_REMOVED`` per missing declaration.

    The detector is intentionally narrow — it never fires when the
    declared name and the underlying name are identical, when the
    declared name is in ``std::``, or when the mangled name does not
    demangle to ``std::``.
    """
    from .model import Visibility

    demangled = _batch_demangle_public(old)
    new_declared = _collect_public_declared_names(new)

    changes: list[Change] = []
    seen: set[str] = set()
    for f in old.functions:
        if f.visibility != Visibility.PUBLIC:
            continue
        declared = _qualified_function_name(f.name, f.mangled, demangled)
        if not declared or declared in seen or declared in new_declared:
            continue
        underlying = demangled.get(f.mangled, "")
        if not _looks_like_std_reexport(declared, underlying):
            continue
        seen.add(declared)
        changes.append(_build_std_reexport_change(declared, underlying))

    return changes


# ---------------------------------------------------------------------------
# Detector: versioned inline namespace bumped (header-declared)
# ---------------------------------------------------------------------------


def detect_inline_namespace_version_bump(
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> list[Change]:
    """Detect declarations whose versioned inline-namespace segment shifted.

    Complementary to the existing symbol-level ``INLINE_NAMESPACE_MOVED``
    detector (``diff_platform._diff_inline_namespace``): that one needs
    ≥2 mangled-symbol moves and works only on built shared libraries;
    this one fires from declared qualified names so it works for header-
    only / template-library snapshots and on a single declaration.

    The detector matches old and new declarations by the *version-
    stripped* qualified name. If both sides have versioned segments AND
    the integer suffix changed, emit one finding per moved declaration.
    """
    old_idx = _index_versioned(_collect_versioned_entries(old))
    new_idx = _index_versioned(_collect_versioned_entries(new))
    return _emit_version_bumps(old_idx, new_idx)


def _index_versioned(
    items: list[tuple[str, bool]],
) -> dict[tuple[str, ...], list[tuple[str, int, bool]]]:
    """Map version-stripped segments → list of ``(qualified, version_int, is_public)``."""
    out: dict[tuple[str, ...], list[tuple[str, int, bool]]] = {}
    for qname, is_public in items:
        segs = _segments(qname)
        stripped, ver = _version_strip_segments(segs)
        if ver is None:
            continue
        out.setdefault(stripped, []).append((qname, ver, is_public))
    return out


def _collect_versioned_entries(snap: AbiSnapshot) -> list[tuple[str, bool]]:
    """Return ``[(qualified_name, is_reliably_public), …]`` for *snap*.

    A function entry is reliably public because it was filtered to
    ``Visibility.PUBLIC`` above. A type entry is reliably public only when
    ``RecordType.origin == ScopeOrigin.PUBLIC_HEADER`` (ADR-024's opt-in
    public-header scoping, ``--public-header``/``--public-header-dir``) —
    the one signal that *does* exist for a type in the absence of a
    visibility field (Codex review). Without that opt-in flag every type's
    ``origin`` is ``ScopeOrigin.UNKNOWN``, so this degrades to the prior
    untagged behavior automatically, not a regression for the common case.
    """
    from .model import ScopeOrigin, Visibility

    demangled = _batch_demangle_public(snap)
    items: list[tuple[str, bool]] = []
    for f in snap.functions:
        if f.visibility != Visibility.PUBLIC:
            continue
        qname = _qualified_function_name(f.name, f.mangled, demangled)
        if qname:
            items.append((qname, True))
    for t in snap.types:
        if t.name:
            items.append((t.name, t.origin == ScopeOrigin.PUBLIC_HEADER))
    return items


def _emit_version_bumps(
    old_idx: dict[tuple[str, ...], list[tuple[str, int, bool]]],
    new_idx: dict[tuple[str, ...], list[tuple[str, int, bool]]],
) -> list[Change]:
    changes: list[Change] = []
    for stripped, old_list in old_idx.items():
        new_list = new_idx.get(stripped, [])
        if not new_list:
            continue
        old_versions = {v for _, v, _ in old_list}
        new_versions = {v for _, v, _ in new_list}
        if old_versions == new_versions:
            continue
        if max(new_versions) <= max(old_versions):
            continue
        old_q = old_list[0][0]
        new_q = new_list[0][0]
        # `_collect_versioned_entries` marks a function entry reliably
        # public (Visibility.PUBLIC filter) and a type entry reliably
        # public only when its origin is ScopeOrigin.PUBLIC_HEADER (Codex
        # review) — untagged otherwise, same as before public-header
        # scoping is used. `or`, not `and` (Codex review, fresh evidence):
        # old-side public evidence alone already proves an old-consumer
        # break (an application linked against the old public symbol),
        # regardless of whether the new symbol also has public-header
        # evidence — public-header scoping can be asymmetric between two
        # snapshots (a type moved out of the scoped header set, or the flag
        # only covers one side), so requiring both sides publicly-tagged
        # let a genuine old-consumer break stay untagged and suppressible.
        subject_is_public = old_list[0][2] or new_list[0][2]
        changes.append(
            make_change(
                ChangeKind.INLINE_NAMESPACE_VERSION_BUMPED,
                symbol=new_q,
                old=old_q,
                new=new_q,
                detail=f"{sorted(old_versions)} to {sorted(new_versions)}",
                public_reachable=subject_is_public,
                reachability_state=(
                    ReachabilityState.PROVEN_REACHABLE
                    if subject_is_public
                    else ReachabilityState.UNKNOWN
                ),
                reachability_kind="direct_public_symbol" if subject_is_public else None,
            )
        )
    return changes


# ---------------------------------------------------------------------------
# Combined entry point used by the post-processing pipeline.
# ---------------------------------------------------------------------------


def detect_namespace_patterns(
    old: AbiSnapshot,
    new: AbiSnapshot,
    experimental_namespaces: tuple[str, ...] = DEFAULT_EXPERIMENTAL_NAMESPACES,
) -> list[Change]:
    """Run all namespace-shape detectors and return their concatenated findings."""
    out: list[Change] = []
    out.extend(
        detect_experimental_namespace_changes(
            old,
            new,
            experimental_namespaces=experimental_namespaces,
        )
    )
    out.extend(detect_std_reexport_removed(old, new))
    out.extend(detect_inline_namespace_version_bump(old, new))
    return out
