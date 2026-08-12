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


# A singleton's key is (its own raw ``stripped``, ``leaf``); a genuinely
# merged component's key is (a ``frozenset`` of its members' (stripped,
# identity) pairs, ``leaf``) -- deliberately a different key *shape* so the
# two can never collide (see the frozenset-construction comment below).
# Opaque to every consumer (``_findings_for`` only uses it as a dict key),
# so the exact type is not load-bearing outside this module.
_IndexKey = tuple[object, str]


def _paired_stable_indices(
    old_items: list[_IndexItem],
    new_items: list[_IndexItem],
) -> tuple[dict[_IndexKey, list[str]], dict[_IndexKey, list[str]]]:
    """Build the OLD and NEW ``(key -> [qname, ...])`` indices *jointly*,
    in two layers, so a genuine alias pair resolves to the SAME output key
    on both sides -- merging two spellings that differ only by a versioned
    inline-namespace segment (``v1``, ``_V2``, ``__1``, ...) but ONLY when
    *identity* proves they name the same declaration.

    **Layer 1 -- raw ``(stripped, leaf)`` buckets, unconditional.** This is
    the pre-existing behaviour the experimental/stable-namespace detector
    (``_findings_for``/``_split_experimental``) has always relied on: two
    qnames whose experimental-stripped spelling is byte-identical (most
    commonly ``ns::experimental::sort`` and an unrelated pre-existing
    ``ns::sort``, or two qnames the caller already spelled identically)
    share a bucket regardless of any identity evidence -- that's a
    trivial, exact-string dict-key collision, not an "alias merge" claim
    about anything, and it predates this module's version-segment
    handling entirely. Layer 2 must never bypass or interfere with it.

    **Layer 2 -- version-segment alias merge, evidence-gated.** Groups
    layer-1 *raw keys* (not individual items) by their version-stripped
    canon + leaf, then only unions two raw keys within a canon group when
    *identity* proves it: a versioned *inline* namespace makes one
    declaration reachable under two qualified spellings -- the full path
    (``ns::v1::x``) and the version-elided path (``ns::x``) that
    unqualified lookup from the enclosing scope also resolves to -- but a
    version-shaped segment name is not proof of an *inline* namespace on
    its own (``v1`` is a legal name for an ordinary namespace too, in
    which case ``ns::v1::x`` and ``ns::x`` are two unrelated declarations
    that happen to share a leaf name -- Codex review, P1: collapsing them
    on name shape alone can both hide a genuine value/removal on one
    spelling and misreport the other). Each item's ``identity`` is a value
    from the snapshot's own extraction data that is invariant across an
    inline-namespace's two spellings but not expected to coincide for two
    unrelated declarations (currently only a function's mangled name
    qualifies -- see ``_type_index_items`` for two candidate type
    identities that were tried and falsified) -- ``None`` when no identity
    evidence is available. Two raw keys are only unioned when EACH has
    exactly one distinct identity value among its items and those two
    values are equal -- not merely a non-empty intersection of however
    many identities each raw key's items happen to carry. A raw key with
    no evidence, evidence that never coincides with anything else's, or
    (Codex review, P1, fresh evidence) an *ambiguous* identity set
    spanning more than one distinct value stays its own singleton -- the
    pre-existing double-report is the accepted fallback rather than a
    newly-introduced false suppression.

    **Residual, deliberately-accepted gap** (Codex review, P1, fresh
    evidence): a shared mangled name proves two spellings resolve to the
    *same linked symbol*, not that they are *the same declaration* reached
    two ways through inline-namespace lookup. Two textually distinct
    ``using``-declarations can each independently alias the identical
    underlying function under two unrelated qualified names -- one that
    happens to nest under a version-shaped segment, one that doesn't --
    the same declared-name/underlying-symbol divergence
    ``detect_std_reexport_removed`` is itself built on. Removing only the
    versioned alias would then be wrongly absorbed into the surviving,
    genuinely-unrelated one, because mangled identity is the only evidence
    this function has and cannot by itself distinguish "one declaration,
    two lookup paths" from "two declarations, one shared symbol". No
    stronger evidence is available from any current producer: real
    inline-namespace status is a per-namespace AST/DWARF fact (clang's own
    `-ast-dump=json` exposes `NamespaceDecl.isInline`; DWARF5 similarly
    distinguishes an inline namespace's `DW_TAG_namespace`), but nothing in
    `dumper_castxml.py`/`dumper_clang.py`/`dwarf_snapshot.py` captures it
    onto `AbiSnapshot` today -- closing this needs a producer-side model
    addition, not a cleverer identity check here, and is out of scope for
    a drive-by fix. Accepted as narrow and theoretical (it requires two
    independent re-exports of one symbol to coincidentally land on a
    version-shaped and version-elided pair of the same leaf) against the
    alternative of reopening the exact false-positive this whole function
    exists to close.

    The ambiguous-set case matters because a raw key's items are not
    always one declaration: a header-derived qualified name can omit a
    function's parameter-list signature (see ``_func_index_items``), so
    two distinct overloads land in the very same layer-1 bucket, and that
    bucket's identity set then aggregates both overloads' mangled names.
    Treating that contaminated set as reliable evidence is unsound in
    either direction -- concretely, if OLD has overload A and B sharing
    one raw key (identity set ``{A, B}``) plus a version-elided alias raw
    key that only ever named A (identity set ``{A}``), and NEW keeps only
    B at the unversioned spelling, the two raw keys' identity sets DO
    intersect on A, but that intersection doesn't prove A's alias means
    the same thing as B's raw key -- it would merge all three entries into
    one component, and since NEW still has a (B-only) entry under the
    merged key, ``_findings_for`` would see the merged entity as "still
    present" and silently drop A's removal entirely, alias included.
    Requiring each side of a union to be an unambiguous singleton closes
    this: the contaminated ``{A, B}`` raw key is never eligible to merge
    with anything, so A's alias and A's full spelling both correctly fall
    through to layer 1's raw double-report instead of being absorbed by
    B's survival.

    Splitting the merge decision onto raw *keys* rather than individual
    *items* is what keeps layer 1 and layer 2 from interfering (Codex
    review, fresh finding surfaced by a Hypothesis property test): an
    earlier, single-layer version of this function grouped items directly
    by version-stripped canon, which silently also changed which items
    the *unversioned* experimental/stable-graduation mechanism could see
    co-existing under one key, breaking ``EXPERIMENTAL_GRADUATED``
    detection for the ordinary (no version segment involved at all) case.

    Deciding this *jointly* over the pooled OLD+NEW raw keys (not building
    each snapshot's index independently) closes two distinct false
    positives Codex review found in an independent-per-snapshot version:
    (1) key instability -- a merged component's chosen output key depended
    on encounter order, so the same alias pair listed in a different
    declaration order between old and new could resolve to two different
    keys and read as a spurious removal; (2) membership asymmetry -- when
    only ONE side actually has both spellings coexisting (an extractor
    starting or stopping the duplicate emission), the singleton side kept
    its raw key while the multi-spelling side's key was canonicalized, so
    the two sides disagreed on the key for what is still the very same
    entity. Pooling both sides' raw keys before computing connected
    components fixes both: the same component, and therefore the same
    key, is computed once and reused for whichever side(s) actually
    contain each member.

    A merged (>1 raw key) component's output key is a ``frozenset`` of its
    member raw keys, not a plain string -- deliberately a different key
    *shape* than any singleton's own raw ``(str, str)`` key, so the two
    can never collide (a Hypothesis property test found that an earlier
    ``min(member)``-based choice, while itself order-independent, could
    coincidentally equal a completely unrelated singleton raw key
    elsewhere in the same canon group, silently merging an unrelated
    declaration with no identity evidence into a real alias pair's
    bucket).

    The canon-group key is derived from ``_strip_param_signature(stripped)``,
    not ``stripped`` directly (Codex review, P2): for a function,
    ``stripped`` deliberately keeps its full parameter-list signature
    (needed so two overloads sharing a leaf stay distinct raw keys in
    layer 1 -- see ``_func_index_items``), but ``_segments()`` doesn't
    track ``(``/``)`` depth, so scanning the *signature* for a
    version-shaped segment can strip one out of a parameter's own type
    (``foo(ns::v2::T)``) instead of only the declaration's own scope path.
    Stripping the signature first (only for the layer-2 canon computation,
    never for the layer-1 raw key) removes that surface entirely; it's a
    no-op for a type's signature-free ``stripped``.
    """
    # Layer 1: raw buckets, unconditional, exactly as this module always
    # built them before any version-segment handling existed.
    raw_old: dict[tuple[str, str], list[str]] = {}
    raw_new: dict[tuple[str, str], list[str]] = {}
    raw_identities: dict[tuple[str, str], set[object]] = {}
    for side, items in (("old", old_items), ("new", new_items)):
        target = raw_old if side == "old" else raw_new
        for item in items:
            raw_key = (item.stripped, item.leaf)
            target.setdefault(raw_key, []).append(item.qname)
            if item.identity is not None:
                raw_identities.setdefault(raw_key, set()).add(item.identity)

    # Layer 2: group raw KEYS (not items) by version-stripped canon + leaf.
    # `sorted(...)`, not a bare set -- Python's str hash (and therefore set
    # iteration order) is randomized per-process (PYTHONHASHSEED), so
    # iterating the pooled raw keys unsorted made which spelling ends up
    # first in a merged bucket -- and therefore which one
    # `_emit_experimental_change` reports as the finding's `symbol` --
    # vary run to run for the IDENTICAL input (Codex review, P1, fresh
    # evidence: an exact-name suppression selector could then match in one
    # process and not another). Tuple comparison is lexicographic and has
    # nothing to do with hashing, so this fully determines every
    # downstream dict's insertion order too (Python dicts preserve
    # insertion order).
    canon_groups: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for raw_key in sorted({*raw_old, *raw_new}):
        stripped, leaf = raw_key
        scope_path = _strip_param_signature(stripped)
        canon_segs, _ = _version_strip_segments(_segments(scope_path))
        canon_groups.setdefault(("::".join(canon_segs), leaf), []).append(raw_key)

    old_out: dict[_IndexKey, list[str]] = {}
    new_out: dict[_IndexKey, list[str]] = {}

    def _passthrough(raw_key: tuple[str, str]) -> None:
        if raw_key in raw_old:
            old_out[raw_key] = raw_old[raw_key]
        if raw_key in raw_new:
            new_out[raw_key] = raw_new[raw_key]

    for raw_keys in canon_groups.values():
        if len(raw_keys) == 1:
            _passthrough(raw_keys[0])
            continue
        # Union-find over this (typically 2-4 entry) group of raw keys,
        # unioning any two whose identity sets intersect.
        parent = list(range(len(raw_keys)))

        def _find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for i in range(len(raw_keys)):
            idents_i = raw_identities.get(raw_keys[i])
            # A raw key's identity set spanning more than one distinct value
            # proves the OPPOSITE of what layer 2 needs: it means this raw
            # key already bundles multiple, distinct declarations (an
            # overloaded function whose header-derived name lacks a
            # parameter-list signature, so two overloads share one layer-1
            # bucket -- see `_func_index_items`), not one declaration
            # reachable under one identity. Using that set for a merge
            # decision is unsound either way it points: it can merge an
            # unrelated raw key on an identity that isn't uniquely this raw
            # key's own (Codex review, P1 -- a surviving overload's bucket
            # absorbing a removed sibling overload's alias, hiding the
            # removal), or it can just as easily NOT merge a genuine alias
            # whose shared identity happens to collide with the
            # contamination. Only a raw key with EXACTLY one distinct
            # identity value is unambiguous evidence -- require that on
            # both sides of the comparison, not just a non-empty
            # intersection.
            if not idents_i or len(idents_i) != 1:
                continue
            for j in range(i + 1, len(raw_keys)):
                idents_j = raw_identities.get(raw_keys[j])
                if idents_j and len(idents_j) == 1 and idents_i & idents_j:
                    ri, rj = _find(i), _find(j)
                    if ri != rj:
                        parent[rj] = ri
        components: dict[int, list[int]] = {}
        for i in range(len(raw_keys)):
            components.setdefault(_find(i), []).append(i)
        for members in components.values():
            if len(members) == 1:
                _passthrough(raw_keys[members[0]])
                continue
            merged_keys = [raw_keys[i] for i in members]
            key: _IndexKey = (frozenset(merged_keys), merged_keys[0][1])
            for rk in merged_keys:
                if rk in raw_old:
                    old_out.setdefault(key, []).extend(raw_old[rk])
                if rk in raw_new:
                    new_out.setdefault(key, []).extend(raw_new[rk])

    # Sort every bucket's qname list before returning (Codex review, P1,
    # fresh evidence): sorting the pooled raw KEYS (above) makes which
    # *merged component* a genuine alias pair resolves to deterministic,
    # but says nothing about declaration order WITHIN one raw bucket --
    # two structurally distinct declarations that happen to collapse onto
    # the same (stripped, leaf) raw key (a coincidental exact-string
    # collision, e.g. `ns::experimental::foo` and `experimental::ns::foo`
    # both stripping to `ns::foo`; genuinely different mangled symbols,
    # unrelated to any Layer 2 alias-merge decision) retained plain
    # snapshot/declaration-iteration order, and `_emit_experimental_change`
    # reports whichever qname happens to be first. Unlike PYTHONHASHSEED,
    # that order isn't hash-randomized within one process, but it isn't
    # something this tool controls either -- two independent extractions
    # of nominally the same library can enumerate declarations in a
    # different order (unstable AST/DWARF traversal order, parallel
    # extraction, ...), which would then report a different `symbol` for
    # the identical logical change and could make an exact-name suppression
    # selector match in one run and not another. Sorting makes the choice
    # of "first" a pure function of the qname strings themselves.
    for bucket in old_out.values():
        bucket.sort()
    for bucket in new_out.values():
        bucket.sort()
    return old_out, new_out


def _looks_like_real_mangled_name(mangled: str) -> bool:
    """True when *mangled* carries a recognized ABI name-mangling prefix
    (Itanium ``_Z``, its Mach-O ``__Z`` variant with the extra platform
    leading underscore, or MSVC ``?``) -- proof the string came from the
    compiler's/linker's actual name mangling, not a header-AST producer's
    fallback to the bare declaration name when no real linkage name was
    available (see the caller's docstring for the two concrete fallback
    paths this excludes). Deliberately narrow: a genuine ``extern "C"``
    symbol's real ABI name also carries neither prefix, so it reads as
    "no identity evidence" here too rather than risk trusting a string
    indistinguishable from the fallback -- ``Function`` has no field that
    tells the two apart today.
    """
    return mangled.startswith("_Z") or mangled.startswith("__Z") or mangled.startswith("?")


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
        #
        # `f.mangled` can ALSO be non-empty and still not be a real
        # mangled name (Codex review, P1, fresh evidence): both header-AST
        # producers fall back to the bare, unqualified declaration name
        # when no real linkage name is available --
        # `dwarf_snapshot.py`'s `_process_subprogram` (`mangled =
        # linkage_name or name`, where `name` is the bare `DW_AT_name`,
        # never the qualified one `Function.name` ends up holding) and
        # `dumper_clang.py`'s parser (`mangled = ... or name`, confirmed to
        # fire not just for a plain-C/`extern "C"` declaration but also for
        # an uninstantiated C++ function/method template with no
        # `mangledName` in clang's header AST -- see `tu_merge.py`'s own
        # "second known, accepted limitation" docstring, which documents
        # the identical fallback causing false ODR-merges there). Two
        # structurally unrelated declarations that both hit this fallback
        # then share the same bare leaf as their "mangled" value even
        # though their *scopes* differ -- e.g. an old `preview::v1::foo`
        # and an unrelated new `preview::foo` both falling back to
        # `mangled="foo"` -- exactly the name coincidence this identity
        # check exists to NOT trust. A simple `mangled == name` comparison
        # (mirroring `tu_merge.py`'s own check) only catches this for the
        # direct-clang backend, whose `Function.name` is itself left bare
        # (equal to the fallback) in the common case -- DWARF's
        # `Function.name` is already qualified, so it never equals the
        # bare fallback and that comparison would silently miss the
        # DWARF-sourced case the reviewer's own example demonstrates.
        # Requiring a recognized ABI name-mangling prefix instead
        # (Itanium `_Z`/Mach-O `__Z`, MSVC `?`) proves the string actually
        # came from the compiler's/linker's real mangling rather than a
        # bare-name fallback, regardless of which producer built it, and
        # subsumes the narrower same-string check. Deliberately
        # conservative: a genuine `extern "C"` symbol's real ABI name is
        # its own bare identifier and carries neither prefix, so this also
        # treats that case as "no identity evidence" rather than risk
        # trusting a fallback that looks identical -- the same
        # false-negative-over-false-positive default this module uses
        # throughout, and `Function` carries no field today that
        # distinguishes the two (`is_extern_c` is itself derived from this
        # same unreliable prefix check).
        identity = f.mangled if f.mangled and _looks_like_real_mangled_name(f.mangled) else None
        out.append(_IndexItem(qname, stripped, leaf, identity))
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


def _stable_keys_compatible(a: str, b: str) -> bool:
    """Whether stable-keys *a* and *b* could be two different-completeness
    spellings of the same declaration path, rather than two genuinely
    different declarations that merely share a trailing segment.

    True when the shorter key's own ``"::"``-segments equal the same
    number of *trailing* segments of the longer key -- i.e. one is a
    (possibly bare) suffix of the other (``"check_ranges"`` vs.
    ``"oneapi::dal::detail::check_ranges"``; ``"ns::check_ranges"`` vs.
    ``"check_ranges"``). Two keys that both carry multiple segments but
    diverge anywhere in that trailing run (``"api::sort"`` vs.
    ``"detail::sort"``) are *not* compatible -- deliberately: when a
    dumper backend under-qualifies a name it drops a *prefix*, never
    replaces an inner segment, so a genuine mismatch there means these are
    two different declarations, not two spellings of one.

    **Residual, deliberately-accepted gap** (Codex review, fresh evidence):
    when the shorter key is *bare* (a single segment, identical to the
    leaf itself -- e.g. an alias declared with no namespace context beyond
    the experimental marker, ``experimental::sort`` stripping to bare
    ``"sort"``), this reduces to plain leaf matching, and a genuinely
    unrelated declaration that happens to share both that leaf and (via
    some other aliasing) the removed alias's mangled symbol
    (``experimental::sort`` vs. an unrelated ``detail::sort``) is wrongly
    treated as compatible, suppressing a real removal. There is no further
    string-only signal available to break this tie: a dumper backend
    under-qualifying a name and a coincidental same-leaf collision produce
    *the exact same bare stable-key*, and the two fixes are in direct
    tension -- requiring more than one segment before allowing suppression
    closes this gap but reopens the original reported bug for every alias
    declared with no namespace context beyond ``experimental::`` itself
    (also a real, and more common, shape). This function resolves that
    tension in favor of the originally reported, verified false-positive
    (the far more common shape in practice: a genuinely-unqualified
    top-level experimental alias whose target happens to share a leaf
    *and* be reachable via the identical mangled symbol is a narrow,
    largely theoretical construction). Closing it for real needs evidence
    this module does not have access to -- e.g. a per-declaration source
    location correlated against the target's own definition site -- not a
    cleverer string comparison.
    """
    segs_a, segs_b = _segments(a), _segments(b)
    if not segs_a or not segs_b:
        return False
    n = min(len(segs_a), len(segs_b))
    return segs_a[-n:] == segs_b[-n:]


def _identity_stable_keys(items: list[_IndexItem]) -> dict[object, set[str]]:
    """Flatten a flat ``_IndexItem`` list into ``{identity: {stripped, ...}}``,
    for :func:`_findings_for`'s dumper-qualification-drift suppression check.

    Mirrors what was originally a raw-``f.mangled``-keyed helper, but keyed
    off ``_IndexItem.identity`` instead -- i.e. already gated through
    :func:`_looks_like_real_mangled_name`, not a raw, possibly-fallback
    ``Function.mangled`` -- so this suppression reuses the SAME
    evidence-quality bar layer 2 of :func:`_paired_stable_indices` requires,
    rather than reintroducing the bare-name-fallback false-alias bug that
    bar was built to close. A raw key's items are not always one
    declaration (two overloads can share a layer-1 bucket -- see
    ``_func_index_items``), so this is built from the *flat* item list, not
    from a paired index's already-bucketed values, and each item
    contributes its own ``stripped`` independently.

    Each ``stripped`` value is signature-stripped before being recorded:
    ``_IndexItem.stripped`` deliberately keeps a demangled entry's
    parameter list (needed for layer-1 overload disambiguation), but a
    bare-named declaration demangles to a full signature
    (``"ns::check_ranges()"``) while an already-qualified declared name on
    the other snapshot side never carries one (``"check_ranges"``), so
    comparing those two raw values directly always mismatches on the
    trailing ``"()"`` alone -- the exact false-negative this suppression
    check exists to close, reappearing through a different door.
    """
    out: dict[object, set[str]] = {}
    for item in items:
        if item.identity is not None:
            out.setdefault(item.identity, set()).add(_strip_param_signature(item.stripped))
    return out


def _classify_experimental_event(
    old_exp: list[str],
    old_stable: list[str],
    new_exp: list[str],
    new_stable: list[str],
    *,
    still_linked: bool = False,
) -> str | None:
    """Return ``"graduated"``, ``"removed"``, or ``None`` for a key pair.

    Graduation requires an experimental presence in old AND a new stable
    twin that did not exist before. Removal requires no replacement on
    either side. Everything else is silent.

    *still_linked* — resolved by the caller via identity (mangled-symbol)
    lookup, always ``False`` for the type-sourced path (``RecordType`` has
    no identity evidence -- see ``_type_index_items``) — suppresses a
    would-be "removed" classification: dumper backends can populate
    ``Function.name`` with different qualification for the identical
    linked symbol across snapshot sides, which can change this key's
    bucket without the symbol actually disappearing. A real removal is
    never linked in ``new`` at all, so this never masks a genuine break
    (Codex review finding).
    """
    if not old_exp:
        return None
    if new_exp and new_stable and not old_stable:
        return "graduated"
    if not new_exp and not new_stable and not old_stable:
        return None if still_linked else "removed"
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
    old_index: dict[_IndexKey, list[str]],
    new_index: dict[_IndexKey, list[str]],
    experimental_namespaces: tuple[str, ...],
    kind_label: str,
    *,
    old_origins: dict[str, ScopeOrigin] | None = None,
    new_origins: dict[str, ScopeOrigin] | None = None,
    old_items: list[_IndexItem] | None = None,
    new_items: list[_IndexItem] | None = None,
) -> list[Change]:
    """Walk old/new indices, emitting one finding per classified event.

    *old_origins*/*new_origins* are ``None`` for the function-sourced path
    (always reliably public) or a ``{qualified_name: ScopeOrigin}`` map for
    the type-sourced path (public only when ``ScopeOrigin.PUBLIC_HEADER``).

    *old_items*/*new_items* are the flat (pre-pairing) ``_IndexItem`` lists
    the caller built ``old_index``/``new_index`` from, used only for the
    dumper-qualification-drift suppression check below -- ``None`` (the
    default) disables it entirely, degrading to the pre-suppression
    behaviour. Always empty-equivalent for the type-sourced path since
    ``_type_index_items`` never assigns identity.
    """
    # Grouped by RAW (stripped, leaf) key, not by qname: two overloads can
    # share one identical, undemangled declared qname (`_qualified_
    # function_name` returns a name-with-"::"  as-is, no signature), so a
    # qname-keyed `{qname: identity}` map would silently collapse their two
    # distinct identities onto one (whichever item's entry a dict comp
    # visits last) -- corrupting the per-item correlation the `still_linked`
    # check below depends on (Codex review, fresh evidence: reproduced with
    # two same-qname overloads, one genuinely removed, one surviving under a
    # differently-qualified spelling -- the dict collision let the survivor's
    # identity stand in for the removed sibling's too, wrongly suppressing
    # the real removal). Matching items back to a bucket structurally --
    # by raw-key membership, exactly mirroring how `_paired_stable_indices`
    # itself assembled that bucket -- keeps every item's own identity intact
    # regardless of how many items share a qname string.
    old_by_raw: dict[tuple[str, str], list[_IndexItem]] = {}
    for item in old_items or []:
        old_by_raw.setdefault((item.stripped, item.leaf), []).append(item)
    new_identity_stable_keys = _identity_stable_keys(new_items or [])
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
        # Recover the exact `_IndexItem`s that make up this bucket -- via
        # raw-key membership, not qname -- so `still_linked` below can use
        # each item's own identity even when several items share a qname
        # string. `stable_key` can be a merged `frozenset` of raw keys here
        # (see `_paired_stable_indices`) once two version-segment-aliased
        # spellings have already been unioned into one bucket; a singleton
        # bucket's own key IS its one raw key.
        old_key_raw_keys: frozenset[tuple[str, str]]
        if isinstance(stable_key, frozenset):
            old_key_raw_keys = stable_key
        else:
            assert isinstance(stable_key, str)
            old_key_raw_keys = frozenset({(stable_key, leaf)})
        old_key_items = [
            item for raw_key in old_key_raw_keys for item in old_by_raw.get(raw_key, [])
        ]
        old_exp_items = [
            item
            for item in old_key_items
            if any(s in experimental_namespaces for s in _segments(item.qname))
        ]
        # Independently confirm a would-be "removed" declaration's
        # underlying symbol is genuinely gone under a *compatible* spelling
        # of its own qualified path, rather than merely re-bucketed by a
        # declared-name qualification change between snapshot sides.
        #
        # `all(...)`, not `any(...)` (Codex review, fresh evidence): a
        # bucket can hold *multiple* overloads that share one declared
        # name and therefore one (stable_key, leaf) key when neither side
        # demangles (header-derived Function.name carries no parameter
        # list at all, so two overloads with different mangled symbols
        # collapse into one `old_exp` list). `any(...)` would let a single
        # surviving sibling overload's mangled symbol suppress the *whole*
        # bucket's removal, silently hiding a genuinely removed sibling
        # overload. Requiring every entry to independently correlate to a
        # survivor still suppresses correctly for the common
        # single-overload case (`all` over one element is `any` over one
        # element) while never masking a real removal sitting alongside an
        # unrelated survivor. `old_exp_items` is non-empty whenever `old_exp`
        # is (same underlying declarations, just recovered structurally), so
        # `all(...)` over it is never vacuously true here.
        still_linked = bool(old_exp_items) and all(
            item.identity is not None
            and any(
                _stable_keys_compatible(
                    _strip_param_signature(item.stripped),
                    candidate,
                )
                for candidate in new_identity_stable_keys.get(item.identity, ())
            )
            for item in old_exp_items
        )
        event = _classify_experimental_event(
            old_exp,
            old_stable,
            new_exp,
            new_stable,
            still_linked=still_linked,
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
    old_func_items = _func_index_items(old, experimental_namespaces)
    new_func_items = _func_index_items(new, experimental_namespaces)
    old_func_index, new_func_index = _paired_stable_indices(old_func_items, new_func_items)
    out.extend(
        _findings_for(
            old_func_index,
            new_func_index,
            experimental_namespaces,
            "declaration",
            old_items=old_func_items,
            new_items=new_func_items,
        )
    )
    old_type_items = _type_index_items(old, experimental_namespaces)
    new_type_items = _type_index_items(new, experimental_namespaces)
    old_type_index, new_type_index = _paired_stable_indices(old_type_items, new_type_items)
    out.extend(
        _findings_for(
            old_type_index,
            new_type_index,
            experimental_namespaces,
            "type",
            old_origins=_origin_by_name(old.types),
            new_origins=_origin_by_name(new.types),
            old_items=old_type_items,
            new_items=new_type_items,
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
