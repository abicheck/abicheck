# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""Direct-reference retention for dump-time dependency scoping.

Which dependency-header types and enums a scoped dump keeps because a kept
declaration names them directly: a function's return or parameter type, a
variable's type, or a kept type's own field or base. See the
"Direct-reference retention" paragraph of
:mod:`abicheck.workflows.dump.dependency_scope`. Split out of the former
``dumper_scoping.py`` (lane B, stage B4) to keep each module under the
800-line cap.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Container, Mapping, Sequence, Set as AbstractSet

from ...model import EnumType, Function, RecordType, Variable
from ...type_reachability import (
    _compile_spelling_pattern,
    _namespace_suffix_spellings,
    _stripped_signature_spelling,
    spelling_matches,
    spellings_possible_in,
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
    combined_pattern = _compile_spelling_pattern(
        spellings_possible_in(typedef_keys | interesting_keys, typedefs.values())
    )
    if combined_pattern is None:
        return {alias: frozenset() for alias in typedefs}

    embedded_refs: dict[str, set[str]] = {alias: set() for alias in typedefs}
    reachable: dict[str, set[str]] = {alias: set() for alias in typedefs}
    for alias, target in typedefs.items():
        for match in spelling_matches(combined_pattern, target):
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


def _kept_signature_haystack(
    kept_functions: Sequence[Function],
    kept_variables: Sequence[Variable],
    kept_types: Sequence[RecordType],
) -> str:
    """The joined signature text of everything scoping is keeping anyway.

    Deliberately single-hop: only the kept declarations' own return/parameter/
    variable types and field/base spellings, never a *dependency* candidate's
    own fields/bases -- chasing further re-admits the transitive implementation
    closure (e.g. ``std::string``'s ``_Alloc_hider``) this scoping drops.
    """
    texts: list[str] = []
    for fn in kept_functions:
        texts.append(fn.return_type)
        texts.extend(p.type for p in fn.params)
    for var in kept_variables:
        texts.append(var.type)
    for rec in kept_types:
        texts.extend(f.type for f in rec.fields)
        texts.extend(rec.resolved_bases())
        texts.extend(rec.resolved_virtual_bases())
    # Deduplicated -- see tests/test_dumper_scoping_haystack.py for why.
    return "\n".join(dict.fromkeys(t for t in texts if t))


def _kept_type_spellings(
    kept_types: Sequence[RecordType], kept_enums: Sequence[EnumType]
) -> set[str]:
    """Every spelling a *kept* type or enum could be named by.

    Elaborated-type-specifier spellings (``struct Foo``, ``union Foo``, ``enum
    Foo``) must collision-guard against a kept type's/enum's own spelling the
    same way a dependency candidate's own elaborated spelling does (Codex
    review, fresh evidence): a kept ``api::Foo`` and an unrelated
    dependency-header global ``struct Foo`` share the bare tag name ``Foo``,
    and a declaration inside ``api::`` can spell a reference to the kept type
    as bare ``struct Foo *`` (the same namespace-dropping convention accounted
    for everywhere else in this module) -- but only the bare ``Foo`` suffix was
    ever added here, never its elaborated form, so ``struct Foo`` slipped past
    this guard and let the unrelated dependency ``struct Foo`` be retained as
    if the signature's elaborated reference named it.

    Kept enums were missed entirely by an earlier version of the same guard
    (it checked only *kept_types*); both are included here.
    """
    spellings: set[str] = set()
    for rec in kept_types:
        suffixes = _namespace_suffix_spellings(_candidate_identity(rec))
        spellings.update(suffixes)
        for keyword in _elaborated_tag_keywords(rec):
            spellings.update(f"{keyword} {s}" for s in suffixes)
    for enum in kept_enums:
        suffixes = _namespace_suffix_spellings(_candidate_identity(enum))
        spellings.update(suffixes)
        spellings.update(f"enum {s}" for s in suffixes)
    return spellings


def _raw_candidate_spellings(
    candidate: RecordType | EnumType, identity: str
) -> set[str]:
    """Every spelling one dependency candidate could be named by, unguarded.

    Full identity, namespace-suffix spellings, the stdlib-stripped form, the
    globally-qualified form, and the elaborated tag forms of all of those. The
    caller applies the collision guards.

    The global-scope entry exists because direct-clang preserves a signature's
    explicit global-scope qualifier in its printed ``qualType`` (``void
    f(::dep::Thing *)`` keeps the leading ``::``, and this applies just as much
    to an unnamespaced type -- ``void f(::Foo *)`` -- as to a namespaced one; an
    earlier revision only handled the namespaced case, Codex review, fresh
    evidence). The boundary-aware matcher's negative lookbehind treats ``:`` as
    a non-boundary character (so a spelling can't accidentally match a *partial*
    scope, e.g. ``Thing`` inside ``ns::Thing``), which also means it rejects the
    bare-qualified spelling when immediately preceded by the extra ``:`` of a
    leading ``::``. Registering the fully global-qualified spelling explicitly
    lets it match on its own, without weakening the boundary check. Namespace-
    suffix spellings are never meaningfully global-qualified this way
    (``::Thing`` alone isn't how a backend spells a qualifier-dropped
    reference), so only the full identity gets this treatment.

    An elaborated-type-specifier spelling (``struct Handle``, ``union
    Handle``, ``enum Handle``) is unambiguous in both C and C++ regardless of
    any colliding typedef alias of the same bare name (Codex review, fresh
    evidence): tag names and typedef names occupy separate namespaces, and the
    elaborated keyword is exactly what disambiguates a signature writing
    ``struct Handle *`` even when ``typedefs["Handle"] = "int"`` also exists.
    These are added as distinct spellings (never equal to the bare
    identity/suffix string a colliding typedef alias name could match), so they
    naturally fall outside every typedef-alias veto without special-casing.
    """
    stripped = _stripped_signature_spelling(identity)
    spellings = {identity, *_namespace_suffix_spellings(identity)}
    if stripped:
        spellings.add(stripped)
    spellings.add(f"::{identity}")
    # Snapshot the un-elaborated spellings first: ``set.update`` consumes the
    # generator incrementally, so an inline ``set(spellings)`` would be
    # re-evaluated per keyword and the second keyword would see the first one's
    # own output -- yielding double-tagged junk like ``class struct Foo``, and
    # varying with ``_elaborated_tag_keywords``' frozenset iteration order
    # (CodeRabbit review). A record with two legal keywords (``struct``/
    # ``class``) is the common case, not an edge one.
    base_spellings = set(spellings)
    spellings.update(
        f"{keyword} {s}"
        for keyword in _elaborated_tag_keywords(candidate)
        for s in base_spellings
    )
    return spellings


def _kept_touched_alias_names(
    reachable_by_alias: Mapping[str, AbstractSet[str]], kept_spellings: set[str]
) -> set[str]:
    """Alias names whose reachable set touches a kept type's/enum's spelling.

    Deliberately **not** folded into the shared *kept_spellings* set (Codex
    review, fresh evidence): a compound alias like ``using Alias = Pair<api::Own,
    dep::Thing>;`` reaches *both* a kept type and a real, distinct dependency
    candidate in the same target; blanket-excluding the alias's own name via
    *kept_spellings* -- which the final guard checks unconditionally for every
    spelling -- would also erase the alias's legitimate use as ``dep::Thing``'s
    own spelling, not just protect against a coincidental collision. Used
    narrowly instead: only to keep a candidate's *own* bare-suffix spelling from
    coincidentally matching an unrelated kept-pointing alias's name (an
    unrelated ``dep::Handle`` deriving the same bare ``Handle`` as a typedef
    alias pointing at a kept ``api::Own``).

    The derived suffixes matter as much as the literal key (Codex review, fresh
    evidence): a real backend's namespace-dropping convention makes a
    kept-pointing alias's own bare-suffix spelling exactly as capable of
    colliding as its qualified name, and an earlier guard excluded only the
    literal key (``api::Handle``), never its derived suffix (``Handle``).
    """
    names: set[str] = set()
    for alias, reached in reachable_by_alias.items():
        if reached & kept_spellings:
            names.add(alias)
            names.update(_namespace_suffix_spellings(alias)[1:])
    return names


def _typedef_alias_name_spellings(typedefs: dict[str, str]) -> set[str]:
    """Every spelling any typedef alias could be known by, resolving or not.

    A typedef alias's own name is a collision claim on its spelling regardless
    of what its target resolves to (Codex review, fresh evidence):
    :func:`_kept_touched_alias_names` records only an alias whose target reaches
    a kept type/enum, and :func:`_alias_spelling_owners` only one reaching a
    dependency candidate -- an alias to a primitive (``typedefs["Handle"] =
    "int"``) reaches neither, so it was never recorded anywhere, and an
    unrelated ``dep::Handle`` deriving the identical bare suffix ``Handle`` was
    retained as if the signature's ``Handle`` genuinely named it, even though it
    unambiguously names the primitive alias instead. A typedef alias existing
    under a given name at all means a signature spelling that name means *that
    alias* first; only when the alias's own reachability separately resolves to
    a candidate does that candidate still get credit for the spelling.

    Applied only as a *final* veto on a candidate's weakest-tier (derived-only)
    claim -- never folded into the per-candidate spellings upstream the way
    :func:`_kept_touched_alias_names` is: a self-referential alias (``typedefs =
    {"Foo0": "struct Foo0"}``) legitimately reaches its own matching candidate
    via the alias-reach lookup, and that lookup keys off the candidate spelling
    index -- blanket-stripping every typedef-alias-named spelling upstream would
    remove the very key it needs to rediscover that legitimate self-reference,
    silently losing the candidate entirely (confirmed empirically: doing so
    broke the existing self-referential-typedef regression test).
    """
    names: set[str] = set()
    for alias in typedefs:
        names.add(alias)
        names.add(f"::{alias}")
        names.update(_namespace_suffix_spellings(alias)[1:])
    return names


def _alias_reach_identities(
    reachable_by_alias: Mapping[str, AbstractSet[str]],
    key_owners: Mapping[str, set[str]],
) -> dict[str, set[str]]:
    """Per alias, the dependency-candidate identities its own reachability resolves to.

    Computed for *every* alias in ``typedefs``, not only ones already known to
    reach something (Codex review, fresh evidence: an earlier revision built
    this per-identity, from only the aliases that already resolved to one, which
    made a *different*, colliding alias of the same spelling that reaches
    nothing retainable at all -- an alias to a primitive, or one whose only
    reached key is itself already ambiguous among dep_candidates -- invisible to
    the ambiguity check; a genuinely ambiguous spelling was then retained as if
    only the resolving alias existed).

    A reached key that is itself already ambiguous among dep_candidates (more
    than one owner -- e.g. a namespace-stripped target token ``Thing`` shared by
    both ``dep1::Thing`` and ``dep2::Thing``) is excluded the same way the
    candidate spelling sets already exclude a colliding key: the alias's target
    contained one ambiguous token, not two distinct ones the way a genuine
    compound alias (``Pair<dep::A, dep::B>``) does.
    """
    reach: dict[str, set[str]] = {}
    for alias, reached in reachable_by_alias.items():
        identities: set[str] = set()
        for key in reached & key_owners.keys():
            owners = key_owners[key]
            if len(owners) == 1:
                identities |= owners
        reach[alias] = identities
    return reach


def _alias_spelling_owners(
    typedefs: dict[str, str], alias_reach: dict[str, set[str]]
) -> dict[str, set[str]]:
    """Spelling -> identities every alias contributing that spelling agrees on.

    A spelling maps back to *every* alias in ``typedefs`` that could produce it
    -- literal name, namespace suffix, or globally-qualified form (direct-clang
    preserves an explicit global-scope qualifier, ``void f(::Handle);``, the
    same way it does for a direct type reference) -- regardless of whether that
    alias resolves to anything, so a non-resolving alias's collision is never
    invisible here.

    The result is the *intersection* of each contributing alias's own reach
    (Codex review, fresh evidence: an earlier revision required every
    contributing alias's reach to be the *identical* set, which incorrectly
    dropped merely *partial* agreement -- ``api::Handle -> Pair<dep::A,
    dep::B>`` and ``vendor::Handle -> dep::A`` both, unambiguously, could mean
    ``dep::A`` regardless of which alias ``Handle`` denotes, even though only
    one also reaches ``dep::B``). Intersecting subsumes the single-alias case
    (nothing to intersect against, so the alias's own reach passes through
    unchanged -- exactly how a genuine compound alias still retains both) and
    naturally empties out whenever any contributing alias reaches nothing at all
    (a primitive, or an alias whose only reached key was itself ambiguous) --
    the same conservative outcome a separate non-resolving-alias veto previously
    enforced, now free.
    """
    sources: dict[str, set[str]] = {}
    for alias in typedefs:
        for spelling in {alias, f"::{alias}", *_namespace_suffix_spellings(alias)[1:]}:
            sources.setdefault(spelling, set()).add(alias)
    owners: dict[str, set[str]] = {}
    for spelling, aliases in sources.items():
        common = set.intersection(*(alias_reach[a] for a in aliases))
        if common:
            owners[spelling] = common
    return owners


def _resolve_spelling_index(
    own_spelling_owners: dict[str, set[str]],
    alias_spelling_owners: dict[str, set[str]],
    kept_spellings: set[str],
    typedef_alias_names: set[str],
) -> dict[str, set[str]]:
    """spelling -> the identities that spelling may be trusted to name.

    A spelling that is some candidate's own identity/suffix is trusted only when
    unambiguous among all owners of that same category, and not colliding with a
    kept type's/enum's own spelling; a spelling reached via alias reachability
    keeps every distinct owner the alias legitimately reaches.

    A typedef alias existing under a given spelling **always** takes precedence
    over any of a candidate's own claims on that spelling -- exact identity match
    or merely derived -- never merged or compared against them (Codex review,
    fresh evidence, generalizing an earlier, narrower rule that deferred only a
    *derived* own-claim, not an *exact* one): in C, tag names (``struct
    Handle``) and typedef names occupy separate namespaces, so a signature
    spelling the bare, unqualified ``Handle`` can only mean an existing typedef
    of that name -- a same-named tag is never reachable that way at all,
    regardless of whether its own identity happens to be an exact or
    merely-derived match for the same string. Concretely: ``typedef struct
    Actual Handle;`` alongside an unrelated ``struct Handle`` must retain
    ``Actual`` through the alias, and never conflate that with the unrelated
    tag's own exact-identity claim on the same spelling. When the existing
    typedef doesn't itself resolve to anything retainable (an alias to a
    primitive, or one dropped as genuinely ambiguous among colliding aliases),
    the spelling contributes nothing at all -- not even the runner-up
    own-identity claim, unconditionally.

    Only when *no* typedef exists under a spelling at all does this fall back to
    plain own-identity resolution: a claim is trusted only when it is the
    spelling's sole owner (two identities colliding is still genuine,
    unresolvable ambiguity).
    """
    index: dict[str, set[str]] = {}
    for spelling in own_spelling_owners.keys() | alias_spelling_owners.keys():
        if spelling in kept_spellings:
            continue
        if spelling in typedef_alias_names:
            alias_owners = alias_spelling_owners.get(spelling, set())
            if alias_owners:
                index[spelling] = set(alias_owners)
            # else: the typedef exists but resolves to nothing retainable
            # -- drop, no credit to any own-identity claim either.
            continue
        own_owners = own_spelling_owners.get(spelling, set())
        if len(own_owners) == 1:
            index[spelling] = set(own_owners)
    return index


def _referenced_from_haystack(
    haystack: str, spelling_index: dict[str, set[str]]
) -> set[str]:
    """Scan *haystack* once and collect every identity its spellings name.

    One compiled multi-spelling pattern
    (:func:`abicheck.type_reachability._compile_spelling_pattern`) rather than
    re-scanning once per candidate spelling (Codex review: the naive
    per-spelling scan is O(candidate count x signature size), which becomes
    seconds-to-minutes on the large transitive dependency surfaces --
    SYCL/heavily-templated C++ headers -- this filter exists to make manageable
    in the first place), turning the scan into one O(signature size) pass
    regardless of candidate count.

    A typedef alias (or any other bare spelling) nested strictly inside an
    already-matched elaborated ``struct``/``union``/``enum <name>`` span must not
    contribute its own resolution (Codex review, fresh evidence): ``typedef
    struct Other Foo; void f(struct Foo *);`` means only the tag ``Foo``, never
    the typedef -- in C/C++ an elaborated-type-specifier resolves exclusively
    through the tag namespace, so the compiler never even considers a same-named
    typedef there. Both ``"struct Foo"`` and the bare ``"Foo"`` can match the
    identical text at once via nested matching, and without this filter the bare
    match's alias resolution incorrectly pulled in the typedef's unrelated
    target alongside the correctly-resolved tag.
    """
    pattern = _compile_spelling_pattern(
        spellings_possible_in(spelling_index, (haystack,))
    )
    if pattern is None:
        return set()
    matches = spelling_matches(pattern, haystack)
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
    haystack = _kept_signature_haystack(kept_functions, kept_variables, kept_types)
    typedefs = typedefs or {}
    kept_spellings = _kept_type_spellings(kept_types, kept_enums)

    identity_of: dict[int, str] = {}
    raw_own_spellings_of: dict[int, set[str]] = {}
    # matching key (any of a candidate's own spellings) -> every identity it
    # could belong to -- built once so resolved typedef targets are scanned once
    # in total, not once per dependency candidate (Codex review, fresh evidence:
    # the naive per-candidate scan over every resolved target is
    # O(dep_candidates x typedefs), confirmed empirically at ~5.6s for 3,000
    # candidates x 3,000 typedefs). This first pass excludes only keys colliding
    # with the base *kept_spellings*; the second pass below additionally excludes
    # keys colliding with a kept-touched typedef alias name once that is known
    # (this preliminary map only feeds the reachability computation).
    prelim_key_owners: dict[str, set[str]] = {}
    for candidate in dep_candidates:
        identity = _candidate_identity(candidate)
        identity_of[id(candidate)] = identity
        spellings = _raw_candidate_spellings(candidate, identity)
        raw_own_spellings_of[id(candidate)] = spellings
        for key in spellings:
            if key not in kept_spellings:
                prelim_key_owners.setdefault(key, set()).add(identity)

    # Which of each typedef alias's transitively-reachable keys are
    # dependency-candidate keys vs. kept-type/enum spellings -- computed by
    # reachability (see _typedef_alias_reachability), never by materializing
    # expanded text.
    reachable_by_alias = _typedef_alias_reachability(
        typedefs, set(prelim_key_owners) | kept_spellings
    )
    kept_touched_aliases = _kept_touched_alias_names(reachable_by_alias, kept_spellings)
    typedef_alias_names = _typedef_alias_name_spellings(typedefs)

    key_owners: dict[str, set[str]] = {}
    own_spelling_owners: dict[str, set[str]] = {}
    kept_spellings_and_aliases = kept_spellings | kept_touched_aliases
    for candidate in dep_candidates:
        identity = identity_of[id(candidate)]
        for spelling in raw_own_spellings_of[id(candidate)]:
            if spelling in kept_spellings_and_aliases:
                continue
            key_owners.setdefault(spelling, set()).add(identity)
            own_spelling_owners.setdefault(spelling, set()).add(identity)

    spelling_index = _resolve_spelling_index(
        own_spelling_owners,
        _alias_spelling_owners(
            typedefs, _alias_reach_identities(reachable_by_alias, key_owners)
        ),
        kept_spellings,
        typedef_alias_names,
    )
    return _referenced_from_haystack(haystack, spelling_index)


def _name_matches(name: str, kept_identifiers: Container[str]) -> bool:
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
