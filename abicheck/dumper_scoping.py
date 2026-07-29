# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""Dump-time dependency scoping (``dump --include-dependencies`` opt-out).

A header-AST dump serializes every declaration the parser saw, including the
entire transitive dependency surface pulled in by ``#include`` (every
libstdc++/SYCL internal a public *or* private header happens to reach) —
for a library with a large or heavily-templated dependency stack this can
put the snapshot JSON in the hundreds-of-MB range, most of which is
dependency surface that belongs to the toolchain/standard library, not to
the library under test.

This is deliberately **not** a public-API-surface filter: the library's own
private/internal declarations are kept, same as its public ones — only
declarations whose own defining header is a toolchain/system header
(``/usr/include``, the MSVC ``VC/Tools`` tree, the Xcode/macOS SDK, ...) are
excluded, and even that is overridden whenever the header is one of the
dump's own ``-H``/``--header`` roots (or lives under one) — see
:func:`provenance.is_dependency_header`'s docstring for why an installed
library analyzed via its real system-prefixed install path
(``-H /usr/include/mylib/api.h``) must not have its own headers misread as
toolchain headers (Codex review). This applies **by default**, without
requiring a ``--public-header``/``--public-header-dir`` set:
``AbiSnapshot.source_header`` is populated unconditionally by
``provenance.apply_provenance``. ``dump --include-dependencies`` opts out
and writes the full, unscoped snapshot (the old default).

Because this scopes by header origin rather than ABI visibility, it is a
silent no-op (not an error) on a snapshot with no header-derived
declarations at all (a binary-only/DWARF-only dump) -- unlike an opt-in
flag, default-on behavior must never fail a plain ``dump`` invocation that
has nothing for it to act on.

**Direct-reference retention (status-review follow-up, closes the P0 flagged
against PR #649):** a dependency-header type/enum that is *directly* named
by a kept (non-dependency) declaration's own signature -- a public
function's return/parameter type, a public variable's type, or a kept
type's own field/base -- is retained even though its own ``source_header``
is a toolchain/system header. This is the dump-time half of the same
direct-vs-transitive distinction :mod:`abicheck.type_reachability` already
draws at diff time: ``void foo(std::string value)`` means the library's ABI
genuinely depends on ``std::string``'s layout, so a scoped dump must not
throw that fact away before ``compare`` ever gets to see it -- unlike
``std::string::_Alloc_hider``, which is reachable only through
``std::string``'s own internals and is dropped exactly as before. Retention
is single-hop only: a directly-referenced dependency type's *own* fields
are not chased for further dependency references, so its private internals
(``_Alloc_hider`` and the like) stay excluded even though the type that
embeds them is kept. See :func:`_directly_referenced_dependency_names`.

**Remaining trade-off, by design (CodeRabbit review):** a genuine
ABI-relevant layout change confined entirely to a dependency type that is
*not* directly referenced anywhere in the kept surface (e.g. an internal
allocator/iterator helper type only reachable through another dependency
type's own internals) still becomes invisible to a later ``compare`` once
both snapshots are scoped -- the type is absent from both sides
symmetrically, not merely demoted. That is the intended effect of "we
don't want a dump of the standard dependency"'s implementation internals,
not a bug, but it does mean `dump`'s default output alone is still not a
toolchain/stdlib ABI-drift detector for *transitively*-reached dependency
internals across compiler or C++ standard library upgrades; pass
``--include-dependencies`` on both sides of a comparison if that detection
is needed.

**Known limitation (investigated, deliberately not fixed here):** this
filters the flat snapshot lists (``functions``/``variables``/``types``/
``enums``) and the DWARF/DWARF-advanced collections keyed off them.
``typedefs`` (``dict[str, str]``, name -> target spelling) carry no
per-entry header provenance at all, so they are kept unconditionally --
typically a small fraction of a dump's size next to full record layouts,
so this is a low-cost simplification, not a hidden accuracy gap the way
skipping type layouts would be. `service._attach_header_graph` (G29 Phase
A, always-on by default -- `_HEADER_GRAPH_ENABLED`) separately embeds a
semantic header-only graph (`snap.build_source.source_graph`, a
`buildsource.source_graph.SourceGraphSummary`) built from the *same*
unscoped header AST; this module leaves it untouched for the same reasons
the previous (now-superseded) public-surface design documented: a correct
filter needs its own closure walk over `GraphNode`/`GraphEdge` (each
carrying its own `facts`/`resolved`/`conflicts`/`provenance`/`confidence`
evidence-merge state -- ADR-046 D2) without corrupting a legitimate real
L3/L4/L5 collection merged into the same pack from an explicit
`--sources`/`--build-info`. That's a separate, independently-scoped
project.
"""

from __future__ import annotations

import dataclasses
from collections import deque
from collections.abc import Sequence
from pathlib import Path

from .dwarf_advanced import AdvancedDwarfMetadata
from .dwarf_metadata import DwarfMetadata
from .model import AbiSnapshot, EnumType, Function, RecordType, Variable, Visibility
from .provenance import is_dependency_header
from .type_reachability import (
    _NON_PUBLIC_ORIGINS,
    _compile_spelling_pattern,
    _finditer_allow_nested,
    _namespace_suffix_spellings,
    _stripped_signature_spelling,
)


def _typedef_alias_reachability(
    typedefs: dict[str, str], interesting_keys: set[str]
) -> dict[str, frozenset[str]]:
    """For every ``typedefs`` alias, which of *interesting_keys* it
    transitively reaches -- whether directly present in its own immediate
    target, or via an embedded reference to another typedef key whose own
    reachable set already includes it.

    Operates on **sets of matched keys**, never on materialized expanded
    text (Codex review, fresh evidence: an earlier version built the full
    decorated expansion string per alias via pointer-doubling substitution
    -- correct for a simple chain, but a *branching* alias, e.g. ``using
    A0 = Pair<A1, A1>; using A1 = Pair<A2, A2>; ...``, doubles the
    materialized string's length at every level, making the "one pass"
    cost actually exponential in nesting depth: confirmed empirically at
    ~38MB of resolved text for just 20 such levels). Because set union is
    idempotent, a branching target that references the same typedef key
    twice (or a hundred times) costs nothing extra here -- the edge is
    recorded once, and propagating a *set* of reachable keys along it is
    bounded by the number of distinct interesting keys, never by how many
    times or how deeply they're nested in the source text.

    Propagation is a monotone fixed-point over the alias reference graph
    (a typedef key embedded in another alias's own immediate target is an
    edge), driven by a reverse-edge worklist so an alias is only
    re-examined when one of its targets gains a new reachable key (see the
    worklist below for why -- a full-table relaxation over a fixed round
    bound was the original approach here and was replaced for being
    quadratic in chain length). A direct self-reference
    (``typedef struct Foo Foo;``) or indirect cycle is never propagated
    through itself (an alias's own name is excluded from its own edge
    set), so it can only ever contribute what's directly present in its
    own immediate target -- terminal by construction, not requiring a
    separate growth guard the way string materialization did.

    *interesting_keys* is deliberately one flat set covering both
    dependency-candidate matching keys and kept-type/enum spellings: the
    caller (:func:`_directly_referenced_dependency_names`) tells which
    category a given reached key belongs to via simple set intersection
    against its own ``key_owners``/``kept_spellings`` -- this function
    only computes *reachability*, not what a reached key means.
    """
    if not typedefs:
        return {}

    typedef_keys = set(typedefs)
    combined_pattern = _compile_spelling_pattern(typedef_keys | interesting_keys)
    if combined_pattern is None:
        return {alias: frozenset() for alias in typedefs}

    embedded_refs: dict[str, set[str]] = {alias: set() for alias in typedefs}
    reachable: dict[str, set[str]] = {alias: set() for alias in typedefs}
    for alias, target in typedefs.items():
        for match in _finditer_allow_nested(combined_pattern, target):
            token = match.group()
            # A token that is itself a *different* typedef key must be
            # resolved through that alias first, not also credited as a
            # direct interesting-key hit (Codex review, fresh evidence):
            # ``typedef int Handle; typedef Handle B;`` alongside an
            # unrelated dependency candidate ``struct Handle`` means the
            # token ``Handle`` inside ``B``'s target is simultaneously a
            # typedef key (resolving to ``int``, an uninteresting
            # terminal) and that candidate's own interesting key -- but
            # ``B`` never actually names the struct, only the *typedef*
            # named ``Handle``, which happens to resolve elsewhere.
            # Crediting both interpretations at once let ``B`` reach the
            # struct regardless of what following the real alias chain
            # would have found. A self-reference (``token == alias``,
            # the classic ``typedef struct Foo Foo;`` idiom already
            # covered by the self-referential-typedef test) is exempt --
            # embedded_refs already excludes it from chain-following, so
            # it is the only way that terminal case can ever be credited.
            if token in typedef_keys and token != alias:
                embedded_refs[alias].add(token)
                continue
            if token in interesting_keys:
                reachable[alias].add(token)

    # Propagate via a reverse-edge worklist rather than a full-table
    # relaxation (Codex review, fresh evidence): the previous approach
    # rescanned every alias's *entire* edge set on every round, up to
    # ``len(typedefs)`` rounds, advancing a long outer-to-inner chain
    # (``A0 -> A1 -> ... -> dep::Thing``) only one hop per round -- measured
    # at ~4.9s for 4,000 chained aliases and ~20.4s for 8,000, i.e.
    # quadratic in chain length. A reverse-edge worklist only re-examines an
    # alias when one of the aliases it points to has just gained a new
    # reachable key, so each edge does work proportional to how many times
    # it actually carries new information, not to the total alias count.
    reverse_refs: dict[str, set[str]] = {alias: set() for alias in typedefs}
    for alias, refs in embedded_refs.items():
        for ref in refs:
            reverse_refs.setdefault(ref, set()).add(alias)

    queue: deque[str] = deque(typedefs)
    queued = set(typedefs)
    while queue:
        node = queue.popleft()
        queued.discard(node)
        for pred in reverse_refs.get(node, ()):
            added = reachable[node] - reachable[pred]
            if added:
                reachable[pred] |= added
                if pred not in queued:
                    queue.append(pred)
                    queued.add(pred)
    return {a: frozenset(v) for a, v in reachable.items()}


#: Elaborated-type-specifier keywords a ``RecordType.kind``/``EnumType``
#: spelling is prefixed with (``struct``/``class``/``union Foo``, ``enum
#: Foo``) -- see the elaborated-spelling generation in
#: ``_directly_referenced_dependency_names`` and its nested-match
#: suppression at the end of that function.
_TAG_KEYWORDS = frozenset({"struct", "class", "union", "enum"})


def _kept_identifiers(names: set[str], qualified_names: set[str]) -> set[str]:
    return names | qualified_names


def _elaborated_tag_keywords(record_or_enum: RecordType | EnumType) -> frozenset[str]:
    """Which elaborated-type-specifier keyword(s) (Codex review, fresh
    evidence) may legally precede a reference to *record_or_enum*'s own
    name. A ``union`` and an ``enum`` each have exactly one legal keyword,
    but C++ permits ``class`` and ``struct`` to refer to the identical
    non-union record type interchangeably (``class Foo`` and ``struct
    Foo`` are the same elaborated-type-specifier for the same ``Foo``) --
    a record declared with one keyword can legally be *referenced* with
    the other, so both spellings are collision-relevant regardless of
    which keyword the declaration itself used.
    """
    if isinstance(record_or_enum, RecordType):
        return (
            frozenset({"struct", "class"})
            if record_or_enum.kind in ("struct", "class")
            else frozenset({record_or_enum.kind})
        )
    return frozenset({"enum"})


def _candidate_identity(candidate: RecordType | EnumType) -> str:
    """The most specific spelling identifying *candidate*: its fully-qualified
    name when the producer populated one, else its bare ``name``."""
    return getattr(candidate, "qualified_name", None) or candidate.name


def _directly_referenced_dependency_names(
    kept_functions: Sequence[Function],
    kept_variables: Sequence[Variable],
    kept_types: Sequence[RecordType],
    kept_enums: Sequence[EnumType],
    dep_candidates: Sequence[RecordType | EnumType],
    typedefs: dict[str, str] | None = None,
) -> set[str]:
    """Which *dep_candidates* (dependency-header types/enums about to be
    dropped) are directly named by a kept, non-dependency declaration's own
    signature -- i.e. reachable at distance one from what
    :func:`scope_snapshot_excluding_dependencies` is keeping anyway, as
    opposed to only reachable transitively through another dependency
    type's own internals. Mirrors
    :func:`abicheck.type_reachability.directly_referenced_stdlib_types`'s
    direct-vs-transitive distinction, but generalized to any dependency
    header (not stdlib-namespace-prefixed only -- e.g. ``struct tm`` from
    ``<time.h>``) since dump-time scoping excludes by header origin, not by
    namespace.

    Deliberately single-hop: only the kept, already-retained declarations'
    own signatures are searched, never a *dependency* candidate's own
    fields/bases -- chasing further would re-admit the transitive
    implementation closure (e.g. ``std::string``'s own
    ``_Alloc_hider`` field) this scoping exists to drop.

    Returns each retained candidate's :func:`_candidate_identity` (not its
    bare ``name``): two dependency candidates can share the same bare name
    under different fully-qualified identities (``std::Thing`` vs.
    ``vendor::Thing``), and returning bare names would let one's match
    re-admit the other's unrelated layout (Codex review).

    Every spelling that could name a candidate -- **including its own full
    identity**, a namespace-suffix spelling from
    :func:`abicheck.type_reachability._namespace_suffix_spellings` (e.g. a
    direct-clang backend's partially-qualified ``Outer::Inner`` for a
    nested ``vendor::Outer::Inner``, or the fully bare leaf), a
    stdlib-stripped spelling from
    :func:`abicheck.type_reachability._stripped_signature_spelling``, or a
    ``typedefs``-resolved alias pointing at any of those -- goes through
    the *same* two collision guards before being trusted: it is dropped if
    it collides with any *kept_types*/*kept_enums* spelling, and dropped if
    two or more *dep_candidates* entries could derive it (mirrors
    ``type_reachability._spelling_index``'s own collision guards). A
    candidate's own full identity is **not** exempt from this (Codex
    review, fresh evidence): when a backend emits a kept type's own
    signature bare (e.g. a kept ``api::Foo`` spelled ``Foo``) and an
    unrelated dependency candidate's identity happens to be that same bare
    ``Foo`` (no namespace of its own), trusting the dependency candidate's
    identity unconditionally would misattribute the kept type's own
    layout. Earlier versions of this guard also missed *kept enum*
    spellings (checked only ``kept_types``, not ``kept_enums``) -- both
    are now included in the same *kept_spellings* set.

    *typedefs* (``AbiSnapshot.typedefs``, alias -> underlying-type spelling)
    is consulted so a dependency type only reachable through a typedef
    alias in the kept signatures is still recognized -- e.g. a signature
    spells ``std::string`` while the record's own identity is the
    underlying, ABI-tag-qualified ``std::__cxx11::basic_string<...>`` and
    the typedef target is itself already namespace/ABI-tag-stripped
    (``basic_string<...>``, DWARF's own convention) -- matched via each
    candidate's stripped form too, not only its exact identity, and via
    each candidate's own namespace-suffix spellings as well (Codex review,
    fresh evidence): castxml's own ``_underlying_type_name()`` stores a
    namespaced typedef target *bare* (``Thing`` for an underlying,
    non-stdlib ``dep::Thing``) -- a producer-side convention distinct from
    (and not covered by) the stdlib-only stripping above, requiring the
    same suffix spellings a kept signature's own spelling of a candidate
    already goes through. An alias chain (``using Handle = Thing; using
    Thing = std::Thing;``) and a *decorated* target (``using Handle =
    std::Thing *;`` -- the target references the candidate identity as a
    substring token, not an exact match) are both resolved via
    :func:`_typedef_alias_reachability`'s graph-based reachability (not
    string substitution -- see that function's own docstring for why),
    rather than requiring exact equality (Codex review, fresh evidence).
    Mirrors :func:`abicheck.type_reachability`'s own typedef-following. A
    typedef alias that transitively reaches a *kept* type's/enum's own
    spelling is tracked as *kept-touched* -- kept **separate** from the
    shared *kept_spellings* set, not folded into it (Codex review, fresh
    evidence): a *compound* alias (``using Alias = Pair<api::Own,
    dep::Thing>;``) can reach both a kept type and a genuinely distinct
    dependency candidate in the same target -- that is not ambiguous (the
    alias names both simultaneously, not "one or the other"), so blanket
    kept-touch exclusion would incorrectly also erase the alias's
    legitimate use as ``dep::Thing``'s own spelling. Kept-touch is instead
    used narrowly to prevent a *different*, genuinely ambiguous
    coincidence: an unrelated dependency candidate's own bare-suffix
    spelling happening to collide with a kept-touched alias's *name*
    (``dep::Handle`` deriving the same bare ``Handle`` as an unrelated
    ``Handle -> api::Own`` typedef) -- only a candidate's own spellings are
    excluded on this basis, never an alias's legitimate
    reachability-derived contribution to a genuinely-referenced candidate. A
    resolved
    typedef alias also contributes its own namespace-suffix spellings (not
    just its exact key) as candidate spellings (Codex review, fresh
    evidence): a real backend can spell the alias itself bare in a
    signature (``string`` for a ``typedefs["std::string"]`` entry, DWARF's
    own convention -- the same bare-vs-qualified split
    :func:`abicheck.type_reachability._typedef_spelling_targets` already
    handles for typedef keys) -- these derived spellings go through the
    same collision guards as everything else in *candidate_spellings*, so
    an ambiguous bare alias suffix is dropped rather than trusted, exactly
    like an ambiguous bare identity suffix.

    Scans the joined signature text with one compiled multi-spelling
    pattern (:func:`abicheck.type_reachability._compile_spelling_pattern`)
    rather than re-scanning it once per candidate spelling (Codex review:
    the naive per-spelling scan is O(candidate count x signature size),
    which becomes seconds-to-minutes on the large transitive dependency
    surfaces -- SYCL/heavily-templated C++ headers -- this filter exists to
    make manageable in the first place), turning the scan into one
    O(signature size) pass regardless of candidate count.

    **Known, inherited limitation (Codex review, not fixed here):** the
    direct-clang backend's ``parse_typedefs()`` (``dumper_clang.py``)
    stores a namespaced typedef under its *bare* key (e.g. ``Handle`` for
    ``namespace api { using Handle = dep::Thing; }``), never the qualified
    ``api::Handle`` form -- this is the exact same producer-side gap
    already documented at length in ``AGENTS.md``'s "A separate, deeper
    finding on typedef keys" entry for :mod:`abicheck.type_reachability`.
    When a kept signature spells the alias qualified (``api::Handle``,
    because Clang prints it qualified from outside its own namespace) the
    boundary-aware match correctly refuses to let the bare key ``Handle``
    match *inside* that qualified spelling (the same guard that prevents
    an unrelated bare ``Thing`` from matching inside ``vendor::Thing``),
    so the dependency candidate stays excluded -- a silent false negative,
    not a false positive. Fixing the root cause means changing
    ``dumper_clang.py`` to store the qualified key, whose blast radius
    reaches every other consumer of ``snapshot.typedefs``
    (``AGENTS.md`` already declines this for the identical reason); a
    local reverse-namespace guesser here would risk fabricating new false
    positives instead. Deliberately left as the same
    false-negative-over-false-positive degradation this whole module
    already uses throughout, consistent with the existing precedent.

    **Sharper consequence of the same root cause (Codex review, fresh
    evidence):** since both ``dumper_clang.py`` and ``dumper_castxml.py``'s
    ``parse_typedefs()`` key ``snapshot.typedefs`` by bare name, two
    *different* namespaced aliases that happen to share one leaf name
    (an own ``api::Handle`` and an unrelated dependency's ``dep::Handle``)
    collide as the same dict key -- Python dict constructions are
    last-write-wins, so one alias's real target is silently and
    completely overwritten by the other's, not merely stripped of its
    namespace. This can misattribute a kept, bare-spelled signature to the
    *wrong* alias's target (the survivor), which is a false positive this
    module's design otherwise avoids -- but the data is already lost by
    the time ``snapshot.typedefs`` reaches this function; no local guard
    here can recover which of the two real aliases the surviving entry
    belongs to. This is not fixable at this layer -- same
    ``parse_typedefs()`` root cause, same declined fix, same blast radius
    as above.
    """
    signature_texts: list[str] = []
    for fn in kept_functions:
        signature_texts.append(fn.return_type)
        signature_texts.extend(p.type for p in fn.params)
    for var in kept_variables:
        signature_texts.append(var.type)
    for rec in kept_types:
        signature_texts.extend(f.type for f in rec.fields)
        signature_texts.extend(rec.bases)
        signature_texts.extend(rec.virtual_bases)
    haystack = "\n".join(t for t in signature_texts if t)

    typedefs = typedefs or {}

    # Elaborated-type-specifier spellings (``struct Foo``, ``union Foo``,
    # ``enum Foo``) must collision-guard against a kept type's/enum's own
    # spelling the same way a dependency candidate's own elaborated
    # spelling does (Codex review, fresh evidence): a kept ``api::Foo``
    # and an unrelated dependency-header global ``struct Foo`` share the
    # bare tag name ``Foo``, and a declaration inside ``api::`` can spell
    # a reference to the kept type as bare ``struct Foo *`` (the same
    # namespace-dropping convention already accounted for everywhere
    # else in this module) -- but only the bare ``Foo`` suffix was ever
    # added to *kept_spellings*, never its elaborated form, so ``struct
    # Foo`` slipped past this guard and let the unrelated dependency
    # ``struct Foo`` be retained as if the signature's elaborated
    # reference named it.
    kept_spellings = set()
    for rec in kept_types:
        suffixes = _namespace_suffix_spellings(_candidate_identity(rec))
        kept_spellings.update(suffixes)
        for keyword in _elaborated_tag_keywords(rec):
            kept_spellings.update(f"{keyword} {s}" for s in suffixes)
    for enum in kept_enums:
        suffixes = _namespace_suffix_spellings(_candidate_identity(enum))
        kept_spellings.update(suffixes)
        kept_spellings.update(f"enum {s}" for s in suffixes)

    identity_of: dict[int, str] = {}
    raw_own_spellings_of: dict[int, set[str]] = {}
    # matching key (any of a candidate's own spellings -- full identity,
    # namespace suffix, or stdlib-stripped form) -> every identity it could
    # belong to -- built once so resolved typedef targets are scanned once
    # in total (below), not once per dependency candidate (Codex review,
    # fresh evidence: the naive per-candidate scan over every resolved
    # target is O(dep_candidates x typedefs), confirmed empirically at
    # ~5.6s for 3,000 candidates x 3,000 typedefs). Namespace-suffix
    # spellings are included here, not just the full identity/stdlib-
    # stripped form (Codex review, fresh evidence): castxml's own
    # ``_underlying_type_name()`` stores a namespaced typedef target bare
    # (``Thing`` for an underlying ``dep::Thing``), which only a suffix key
    # can match -- the same bare-vs-qualified split already applied to a
    # kept signature's own spelling of a candidate. This first pass excludes
    # only keys colliding with the base *kept_spellings*; a second pass
    # below additionally excludes keys colliding with a *kept-touched
    # typedef alias name* once that's known (this preliminary key_owners
    # only feeds the reachability computation).
    prelim_key_owners: dict[str, set[str]] = {}
    for candidate in dep_candidates:
        identity = _candidate_identity(candidate)
        identity_of[id(candidate)] = identity
        stripped = _stripped_signature_spelling(identity)
        spellings = {identity, *_namespace_suffix_spellings(identity)}
        if stripped:
            spellings.add(stripped)
        # direct-clang preserves a signature's explicit global-scope
        # qualifier in its printed ``qualType`` (``void f(::dep::Thing *)``
        # keeps the leading ``::``, and this applies just as much to an
        # unnamespaced type -- ``void f(::Foo *)`` -- as to a namespaced
        # one; an earlier revision of this fix only handled the namespaced
        # case, Codex review, fresh evidence) -- but the boundary-aware
        # matcher's negative lookbehind treats ``:`` as a non-boundary
        # character (so a spelling can't accidentally match a *partial*
        # scope, e.g. matching ``Thing`` inside ``ns::Thing``), which also
        # means it rejects the bare-qualified spelling (``dep::Thing`` or
        # bare ``Foo``) when it's immediately preceded by the extra ``:``
        # of a leading ``::``. Registering the fully global-qualified
        # spelling explicitly lets it match on its own, without weakening
        # the boundary check itself. Namespace-suffix spellings are never
        # meaningfully global-qualified this way (``::Thing`` alone isn't
        # how a backend spells a qualifier-dropped reference), so only the
        # full identity gets this treatment.
        spellings.add(f"::{identity}")
        # An elaborated-type-specifier spelling (``struct Handle``,
        # ``union Handle``, ``enum Handle``) is unambiguous in both C and
        # C++ regardless of any colliding typedef alias of the same bare
        # name (Codex review, fresh evidence): tag names and typedef
        # names occupy separate namespaces, and the elaborated keyword is
        # exactly what disambiguates a signature that writes ``struct
        # Handle *`` even when ``typedefs["Handle"] = "int"`` also
        # exists. These are added as distinct spellings (never equal to
        # the bare identity/suffix string a colliding typedef alias name
        # could ever match), so they naturally fall outside every
        # typedef-alias veto below without needing any special-casing
        # there.
        tag_keywords = _elaborated_tag_keywords(candidate)
        spellings.update(
            f"{keyword} {s}" for keyword in tag_keywords for s in set(spellings)
        )
        raw_own_spellings_of[id(candidate)] = spellings
        for key in spellings:
            if key in kept_spellings:
                continue
            prelim_key_owners.setdefault(key, set()).add(identity)

    # Which of each typedef alias's transitively-reachable keys are
    # dependency-candidate keys vs. kept-type/enum spellings -- computed by
    # reachability (see _typedef_alias_reachability), never by
    # materializing expanded text.
    reachable_by_alias = _typedef_alias_reachability(
        typedefs, set(prelim_key_owners) | kept_spellings
    )
    # An alias whose reachable set touches any kept spelling is recorded
    # here (not folded into the shared *kept_spellings* set -- Codex
    # review, fresh evidence: a compound alias like ``using Alias =
    # Pair<api::Own, dep::Thing>;`` reaches *both* a kept type and a real,
    # distinct dependency candidate in the same target; blanket-excluding
    # the alias's own name via *kept_spellings* -- which the final guard
    # below checks unconditionally for every spelling -- would also erase
    # the alias's legitimate use as ``dep::Thing``'s own spelling, not just
    # protect against a coincidental collision). *kept_touched_aliases* is
    # instead used narrowly: only to keep a candidate's *own* bare-suffix
    # spelling from coincidentally matching an unrelated kept-pointing
    # alias's name (the actual bug this guard exists for -- an unrelated
    # ``dep::Handle`` deriving the same bare ``Handle`` as a typedef alias
    # that points at a kept ``api::Own``) -- an alias's legitimate
    # reachability-derived contribution to a genuinely-referenced
    # candidate's spellings, below, is never subject to this exclusion.
    kept_touched_aliases: set[str] = set()
    for alias, reached in reachable_by_alias.items():
        if reached & kept_spellings:
            kept_touched_aliases.add(alias)
            # A real backend's namespace-dropping convention means a
            # kept-pointing alias's own bare-suffix spelling is exactly as
            # capable of coincidentally colliding with an unrelated
            # candidate's own bare-suffix spelling as its literal
            # qualified name is (Codex review, fresh evidence): the
            # earlier guard only ever excluded the literal alias key
            # (``api::Handle``), never its derived suffix (``Handle``),
            # so a signature spelling the bare-dropped form let an
            # unrelated ``dep::Handle`` slip past this guard entirely.
            kept_touched_aliases.update(_namespace_suffix_spellings(alias)[1:])

    # Every typedef alias's own name is a collision claim on its spelling
    # regardless of what its target resolves to (Codex review, fresh
    # evidence): ``kept_touched_aliases`` above only recorded an alias
    # whose target reaches a kept type/enum or a dependency candidate --
    # an alias to a primitive (``typedefs["Handle"] = "int"`` or ``"void
    # *"``) reaches neither, so it was never recorded anywhere, and an
    # unrelated ``dep::Handle`` deriving the identical bare suffix
    # ``Handle`` was retained as if the signature's ``Handle`` genuinely
    # named it -- even though it unambiguously names the primitive alias
    # instead. A typedef alias existing under a given name at all means a
    # signature spelling that name means *that alias* first; only when
    # the alias's own reachability separately, legitimately resolves to a
    # candidate (via *alias_spelling_owners*, below) does that candidate
    # still get credit for the spelling.
    #
    # This must be applied only as a *final* veto on a candidate's own
    # weakest-tier (derived-only, no alias resolution) spelling claim
    # below -- not folded into *own_spellings_of*/*key_owners* above the
    # way *kept_touched_aliases* is: a self-referential alias (``typedefs
    # = {"Foo0": "struct Foo0"}``) legitimately reaches its own matching
    # candidate via *alias_reach*/*alias_spelling_owners* below, but that
    # reachability lookup keys off ``key_owners`` -- blanket-stripping
    # every typedef-alias-named spelling from ``own_spellings_of``
    # upstream would remove the very key that lookup needs to rediscover
    # that legitimate self-reference, silently losing the candidate
    # entirely (confirmed empirically: doing so broke the existing
    # self-referential-typedef regression test).
    all_typedef_alias_names: set[str] = set()
    for alias in typedefs:
        all_typedef_alias_names.add(alias)
        all_typedef_alias_names.add(f"::{alias}")
        all_typedef_alias_names.update(_namespace_suffix_spellings(alias)[1:])

    key_owners: dict[str, set[str]] = {}
    own_spellings_of: dict[int, set[str]] = {}
    for candidate in dep_candidates:
        identity = identity_of[id(candidate)]
        kept_spellings_and_aliases = kept_spellings | kept_touched_aliases
        spellings = {
            s
            for s in raw_own_spellings_of[id(candidate)]
            if s not in kept_spellings_and_aliases
        }
        own_spellings_of[id(candidate)] = spellings
        for key in spellings:
            key_owners.setdefault(key, set()).add(identity)

    # Every dependency-candidate identity a typedef alias's own
    # reachability resolves to, computed directly per alias -- for *every*
    # alias in ``typedefs``, not only ones already known to reach
    # something (Codex review, fresh evidence: an earlier revision built
    # this per-identity, from only the aliases that already resolved to
    # one, which made a *different*, colliding alias of the same spelling
    # that reaches nothing retainable at all -- an alias to a primitive,
    # or one whose only reached key is itself already ambiguous among
    # dep_candidates -- invisible to the ambiguity check below; a
    # genuinely ambiguous spelling was then retained as if only the
    # resolving alias existed). A reached key that is itself already
    # ambiguous among dep_candidates (``key_owners[key]`` has more than
    # one owner -- e.g. a namespace-stripped target token ``Thing``
    # shared by both ``dep1::Thing`` and ``dep2::Thing``) is excluded the
    # same way ``own_spellings_of``'s own construction already excludes a
    # colliding key -- the alias's target contained one ambiguous token,
    # not two distinct ones the way a genuine compound alias
    # (``Pair<dep::A, dep::B>``) does.
    alias_reach: dict[str, set[str]] = {}
    for alias, reached in reachable_by_alias.items():
        identities: set[str] = set()
        for key in reached & key_owners.keys():
            owners = key_owners[key]
            if len(owners) == 1:
                identities |= owners
        alias_reach[alias] = identities

    # Every spelling a typedef alias could be known by -- literal name,
    # namespace suffix, or globally-qualified form (direct-clang preserves
    # an explicit global-scope qualifier, ``void f(::Handle);``, the same
    # way it does for a direct type reference) -- mapped back to *every*
    # alias in ``typedefs`` that could produce it, regardless of whether
    # that alias resolves to anything (built from ``typedefs`` directly,
    # same universe as *alias_reach* above, so a non-resolving alias's
    # collision is never invisible here).
    alias_spelling_sources: dict[str, set[str]] = {}
    for alias in typedefs:
        spellings = {alias, f"::{alias}", *_namespace_suffix_spellings(alias)[1:]}
        for spelling in spellings:
            alias_spelling_sources.setdefault(spelling, set()).add(alias)

    # spelling -> identities every contributing alias agrees the spelling
    # could mean -- the *intersection* of each contributing alias's own
    # reach (Codex review, fresh evidence: an earlier revision required
    # every contributing alias's reach to be the *identical* set, which
    # incorrectly dropped a merely *partial* agreement -- ``api::Handle ->
    # Pair<dep::A, dep::B>`` and ``vendor::Handle -> dep::A`` both,
    # unambiguously, could mean ``dep::A`` regardless of which alias
    # ``Handle`` denotes, even though only one of them also reaches
    # ``dep::B``). Intersecting also subsumes the single-alias case
    # (nothing to intersect against, so the alias's own reach passes
    # through unchanged -- this is exactly how a genuine compound alias
    # like ``Pair<dep::A, dep::B>`` still retains both) and naturally
    # empties out whenever any contributing alias reaches nothing at all
    # (a primitive, or an alias whose only reached key was itself
    # ambiguous) -- the same conservative outcome a separate
    # non-resolving-alias veto previously existed to enforce, now free.
    alias_spelling_owners: dict[str, set[str]] = {}
    for spelling, aliases in alias_spelling_sources.items():
        common = set.intersection(*(alias_reach[a] for a in aliases))
        if common:
            alias_spelling_owners[spelling] = common

    own_spelling_owners: dict[str, set[str]] = {}
    for candidate in dep_candidates:
        identity = identity_of[id(candidate)]
        own = own_spellings_of[id(candidate)]
        for spelling in own:
            own_spelling_owners.setdefault(spelling, set()).add(identity)

    # spelling -> {identity, ...}: a spelling that is some candidate's own
    # identity/suffix is trusted only when unambiguous among all owners of
    # that same category, and not colliding with a kept type's/enum's own
    # spelling; a spelling reached via alias reachability keeps every
    # distinct owner the alias legitimately reaches.
    #
    # A typedef alias existing under a given spelling **always** takes
    # precedence over any of a candidate's own claims on that spelling --
    # exact identity match or merely derived (namespace-suffix/stdlib-
    # stripped) -- never merged or compared against them (Codex review,
    # fresh evidence, generalizing an earlier, narrower version of this
    # rule that only deferred a *derived* own-claim, not an *exact* one):
    # in C, tag names (``struct Handle``) and typedef names occupy
    # separate namespaces, so a signature spelling the bare, unqualified
    # ``Handle`` can only mean an existing typedef of that name -- a
    # same-named tag is never reachable that way at all, regardless of
    # whether its own identity happens to be an exact or merely-derived
    # match for the same string. Concretely: ``typedef struct Actual
    # Handle;`` alongside an unrelated ``struct Handle`` must retain
    # ``Actual`` through the alias, and never conflate that with the
    # unrelated tag's own exact-identity claim on the same spelling.
    # When the existing typedef doesn't itself resolve to anything
    # retainable (an alias to a primitive, or one dropped as genuinely
    # ambiguous among colliding aliases), the spelling contributes
    # nothing at all -- not even the runner-up own-identity claim,
    # unconditionally.
    #
    # Only when *no* typedef exists under a spelling at all does this
    # fall back to plain own-identity resolution: an exact-identity claim
    # is trusted only when it is the spelling's sole owner (two exact
    # identities colliding is still genuine, unresolvable ambiguity), and
    # a purely-derived claim is trusted only when it, too, is the sole
    # owner among dep_candidates.
    spelling_index: dict[str, set[str]] = {}
    for spelling in own_spelling_owners.keys() | alias_spelling_owners.keys():
        if spelling in kept_spellings:
            continue
        if spelling in all_typedef_alias_names:
            alias_owners = alias_spelling_owners.get(spelling, set())
            if alias_owners:
                spelling_index[spelling] = set(alias_owners)
            # else: the typedef exists but resolves to nothing retainable
            # -- drop, no credit to any own-identity claim either.
            continue
        own_owners = own_spelling_owners.get(spelling, set())
        if len(own_owners) == 1:
            spelling_index[spelling] = set(own_owners)

    pattern = _compile_spelling_pattern(spelling_index)
    if pattern is None:
        return set()
    matches = _finditer_allow_nested(pattern, haystack)
    # A typedef alias (or any other bare spelling) nested strictly inside
    # an already-matched elaborated ``struct``/``union``/``enum <name>``
    # span must not contribute its own resolution (Codex review, fresh
    # evidence): ``typedef struct Other Foo; void f(struct Foo *);`` means
    # only the tag ``Foo``, never the typedef -- in C/C++, an elaborated-
    # type-specifier resolves exclusively through the tag namespace, so
    # the compiler never even considers a same-named typedef there. Both
    # ``"struct Foo"`` (the elaborated spelling) and the bare ``"Foo"``
    # (the typedef alias's own spelling) can match the identical text at
    # once via nested matching, and without this filter the bare match's
    # alias resolution incorrectly pulled in the typedef's unrelated
    # target alongside the correctly-resolved tag.
    elaborated_ends = {
        m.end() for m in matches if m.group().partition(" ")[0] in _TAG_KEYWORDS
    }
    referenced: set[str] = set()
    for match in matches:
        if (
            match.end() in elaborated_ends
            and match.group().partition(" ")[0] not in _TAG_KEYWORDS
        ):
            continue
        referenced.update(spelling_index[match.group()])
    return referenced


def _name_matches(name: str, kept_identifiers: set[str]) -> bool:
    """Exact-only match against the kept types'/enums' own spellings.

    Deliberately does **not** fall back to bare-tail matching (a DWARF key
    ``ns::Foo`` reducing to a bare ``Foo`` and comparing against a kept
    type's bare name): two distinct types sharing a leaf name -- a kept
    ``mine::Thing`` and an excluded ``std::Thing`` -- would otherwise both
    satisfy a tail match against the single bare name ``"Thing"``, letting
    the excluded dependency type's DWARF/DWARF-advanced entry survive the
    filter under its own qualified spelling even though the flat type list
    correctly dropped it (Codex review). ``kept_identifiers`` already
    carries both the bare ``name`` and (when present) the fully-qualified
    ``qualified_name`` of every *kept* type/enum, so an exact match still
    succeeds whichever form a real DWARF/castxml backend spells the same
    kept entity with -- only an actually-ambiguous bare-vs-qualified
    mismatch with no qualified_name recorded at all is missed, the same
    conservative "only drop what's confidently identified" bias the rest
    of this module already uses.
    """
    return name in kept_identifiers


def _scoped_dwarf(
    dwarf: DwarfMetadata | None, kept_identifiers: set[str]
) -> DwarfMetadata | None:
    """Filter a DWARF layout map to the declarations kept from the flat
    ``types``/``enums`` lists (same dependency-exclusion decision, applied
    to the DWARF side so a later ``diff_platform._diff_dwarf`` can't
    silently re-expand to comparing an excluded dependency type's layout)."""
    if dwarf is None or not dwarf.has_dwarf:
        return dwarf
    return dataclasses.replace(
        dwarf,
        structs={
            k: v for k, v in dwarf.structs.items() if _name_matches(k, kept_identifiers)
        },
        enums={
            k: v for k, v in dwarf.enums.items() if _name_matches(k, kept_identifiers)
        },
    )


def _scoped_dwarf_advanced(
    adv: AdvancedDwarfMetadata | None,
    kept_identifiers: set[str],
    kept_symbols: set[str],
) -> AdvancedDwarfMetadata | None:
    """Filter Sprint-4 advanced DWARF metadata the same way: type-keyed
    collections (``packed_structs``/``all_struct_names``) via
    :func:`_name_matches`, function-keyed collections (keyed by mangled
    ``linkage_name``) via *kept_symbols*."""
    if adv is None or not adv.has_dwarf:
        return adv
    return dataclasses.replace(
        adv,
        calling_conventions={
            k: v for k, v in adv.calling_conventions.items() if k in kept_symbols
        },
        value_abi_traits={
            k: v for k, v in adv.value_abi_traits.items() if k in kept_symbols
        },
        return_value_sizes={
            k: v for k, v in adv.return_value_sizes.items() if k in kept_symbols
        },
        return_memory_classified={
            k for k in adv.return_memory_classified if k in kept_symbols
        },
        packed_structs={
            k for k in adv.packed_structs if _name_matches(k, kept_identifiers)
        },
        all_struct_names={
            k for k in adv.all_struct_names if _name_matches(k, kept_identifiers)
        },
        frame_registers={
            k: v for k, v in adv.frame_registers.items() if k in kept_symbols
        },
        callee_saved_regs={
            k: v for k, v in adv.callee_saved_regs.items() if k in kept_symbols
        },
    )


def resolve_dependency_scope(
    snap: AbiSnapshot,
    include_dependencies: bool,
    header_roots: Sequence[Path | str] | None = None,
) -> AbiSnapshot:
    """The single choke point ``dump`` calls right before serialization:
    apply :func:`scope_snapshot_excluding_dependencies` (``dependency_scope``
    ``"filtered"``) unless *include_dependencies* opts out, in which case
    just record the user's actual intent as ``"full"`` (a no-op when there
    are no header-derived declarations to tag at all — see
    ``AbiSnapshot.dependency_scope``'s own docstring)."""
    if not include_dependencies:
        return scope_snapshot_excluding_dependencies(snap, header_roots)
    if not snap.from_headers:
        return snap
    return dataclasses.replace(snap, dependency_scope="full")


def tag_live_dump_dependency_scope(snap: AbiSnapshot) -> AbiSnapshot:
    """``service.run_dump`` (compare's live-binary dumping) never applies
    :func:`scope_snapshot_excluding_dependencies` — tag an untagged,
    header-derived result ``"full"`` so
    ``comparability._check_dependency_scope_comparable`` can tell that
    apart from a snapshot merely predating the ``dependency_scope`` field
    (see its own docstring for why the distinction matters)."""
    if snap.dependency_scope is not None or not snap.from_headers:
        return snap
    return resolve_dependency_scope(snap, include_dependencies=True)


def scope_snapshot_excluding_dependencies(
    snap: AbiSnapshot,
    header_roots: Sequence[Path | str] | None = None,
) -> AbiSnapshot:
    """Return a copy of *snap* with toolchain/system-header declarations
    dropped, keeping everything that belongs to the library itself.

    Keeps a function/variable/type/enum unless its own ``source_header`` is
    a toolchain/system header (see :func:`provenance.is_dependency_header`)
    -- this is a header-*origin* filter, not an ABI-visibility one: a
    private, non-exported declaration from the library's own headers is
    kept exactly like a public one, only dependency-header declarations are
    dropped. ``header_roots`` should be the actual ``-H``/``--header``
    paths the dump was invoked with, so a header that *is* one of them (or
    lives under one, e.g. an installed library's own private headers under
    ``/usr/include/mylib/``) is never misclassified as a dependency just
    because it happens to sit under a system prefix -- pass ``None`` only
    when no such root set is available (falls back to a bare path-heuristic
    check). ``dwarf``/``dwarf_advanced`` are filtered by the same decision
    (see :func:`_scoped_dwarf`/:func:`_scoped_dwarf_advanced`) so a later
    DWARF diff can't silently re-observe an excluded type's layout.

    A no-op (returns *snap* unchanged) when the snapshot has no
    header-derived declarations at all (:attr:`AbiSnapshot.from_headers`
    False -- a binary-only or DWARF-only dump) -- this runs by default, so
    unlike an opt-in flag it must never fail a plain invocation that has
    nothing for it to act on.

    The result is a lossy artifact: a later ``compare`` against it can only
    see what this filter kept, so comparing a scoped snapshot against one
    dumped with ``--include-dependencies`` is not meaningful — scope both
    sides of a comparison the same way.
    """
    if not snap.from_headers:
        return snap

    def _is_dep(source_header: str | None) -> bool:
        return is_dependency_header(source_header, header_roots)

    kept_functions = [f for f in snap.functions if not _is_dep(f.source_header)]
    kept_variables = [v for v in snap.variables if not _is_dep(v.source_header)]
    kept_types = [t for t in snap.types if not _is_dep(t.source_header)]
    kept_enums = [e for e in snap.enums if not _is_dep(e.source_header)]

    dep_types = [t for t in snap.types if _is_dep(t.source_header)]
    dep_enums = [e for e in snap.enums if _is_dep(e.source_header)]
    if dep_types or dep_enums:
        # Direct-reference retention roots are restricted to the *public*
        # subset of kept_functions/kept_variables (kept_functions/
        # kept_variables themselves are NOT narrowed -- every non-dependency
        # declaration still stays in the final snapshot, matching this
        # function's own header-origin-only contract). A hidden/private
        # function or variable naming a dependency type in its own
        # signature must not itself cause that dependency type to be
        # retained: if that private declaration is later removed, the
        # dependency type silently drops out of the *next* scoped
        # snapshot's retention set too, and `compare` then reports a
        # spurious TYPE_REMOVED for a type whose real public-surface
        # relevance never changed (Codex review, fresh evidence). A public
        # declaration's own removal causing the same drop is not a false
        # positive by the same reasoning -- the public surface's real
        # dependency on that type has genuinely ended. Reuses
        # type_reachability._NON_PUBLIC_ORIGINS, the same public-surface
        # predicate its own directly_referenced_stdlib_types() already
        # applies for the identical reason. RecordType/EnumType have no
        # `visibility` field, but both do carry `origin` (ADR-015, schema
        # v6) -- a prior version of this comment incorrectly claimed
        # neither field existed at all and passed kept_types/kept_enums
        # through unfiltered as retention roots, so a kept type/enum whose
        # own header is private/generated/system (but which is still, by
        # this function's header-origin-only contract, retained in the
        # final snapshot) could keep an unrelated dependency type alive
        # through its own fields even though no *public* declaration
        # reaches it (Codex review, fresh evidence). Filtered on `origin`
        # alone here, since there is no `visibility` to additionally check.
        public_root_functions = [
            f
            for f in kept_functions
            if f.visibility == Visibility.PUBLIC and f.origin not in _NON_PUBLIC_ORIGINS
        ]
        public_root_variables = [
            v
            for v in kept_variables
            if v.visibility == Visibility.PUBLIC and v.origin not in _NON_PUBLIC_ORIGINS
        ]
        public_root_types = [
            t for t in kept_types if t.origin not in _NON_PUBLIC_ORIGINS
        ]
        public_root_enums = [
            e for e in kept_enums if e.origin not in _NON_PUBLIC_ORIGINS
        ]
        directly_referenced = _directly_referenced_dependency_names(
            public_root_functions,
            public_root_variables,
            public_root_types,
            public_root_enums,
            [*dep_types, *dep_enums],
            snap.typedefs,
        )
        if directly_referenced:
            kept_types = kept_types + [
                t for t in dep_types if _candidate_identity(t) in directly_referenced
            ]
            kept_enums = kept_enums + [
                e for e in dep_enums if _candidate_identity(e) in directly_referenced
            ]

    kept_identifiers = _kept_identifiers(
        {t.name for t in kept_types} | {e.name for e in kept_enums},
        {t.qualified_name for t in kept_types if t.qualified_name}
        | {e.qualified_name for e in kept_enums if e.qualified_name},
    )
    kept_symbols = {f.mangled for f in kept_functions if f.mangled}
    return dataclasses.replace(
        snap,
        functions=kept_functions,
        variables=kept_variables,
        types=kept_types,
        enums=kept_enums,
        dwarf=_scoped_dwarf(snap.dwarf, kept_identifiers),
        dwarf_advanced=_scoped_dwarf_advanced(
            snap.dwarf_advanced, kept_identifiers, kept_symbols
        ),
        # Records that this snapshot went through dependency-exclusion —
        # comparability.check_contracts_comparable uses this to refuse to
        # compare a filtered snapshot against an unfiltered one (see
        # AbiSnapshot.dependency_scope's own docstring).
        dependency_scope="filtered",
        # dataclasses.replace() otherwise carries these lazy lookup-index
        # caches over from *snap* verbatim: if the input snapshot's index()
        # was already called (e.g. by an earlier pipeline step), the copy
        # would keep pointing at the unscoped functions/types lists even
        # though its own .functions/.types are now filtered, so
        # func_by_mangled()/type_by_name() on the *returned* snapshot could
        # resolve a declaration this scoping just dropped.
        # None forces a lazy rebuild from the scoped lists on next access.
        _func_by_mangled=None,
        _var_by_mangled=None,
        _type_by_name=None,
    )
