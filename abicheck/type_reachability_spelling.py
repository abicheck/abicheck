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

"""Spelling/matching primitives for :mod:`abicheck.type_reachability`.

Split out purely to stay under the AI-readiness file-size hard cap (2000
lines) -- ``type_reachability.py`` had grown past it after several rounds
of Codex-review hardening on its stateful scan machinery
(``_StdlibReferenceScan`` and friends). This module holds the *leaf*, pure
(no scan-state) half: deriving and comparing the various spellings a
stdlib type, non-stdlib record, or typedef alias can appear under in a
real signature/field type string, plus the ``Function``/``Variable``
public-root filter. See ``type_reachability.py``'s own module docstring
for the full feature narrative -- this file is a one-directional leaf
dependency of it, never the reverse, so importing from here never risks
the import-cycle-growth AI-readiness check.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from typing import TYPE_CHECKING

from .diff_cxx_rules import itanium_qualified_name, msvc_qualified_name
from .model import ScopeOrigin, Visibility
from .model.namespace_spelling import (
    # Re-exported (`as`-aliased) by value -- moved to `model/` (ADR-063
    # Track 2, 5B closure) so `compare/vtable_evidence.py` can depend on it
    # without importing this module (which imports `diff_cxx_rules` at
    # module scope, so the reverse import would be a cycle). Every existing
    # `from .type_reachability_spelling import _namespace_suffix_spellings`
    # call site keeps resolving. See `model/namespace_spelling.py`'s own
    # module docstring for the full accounting.
    _namespace_suffix_spellings as _namespace_suffix_spellings,
)
from .name_classification import STDLIB_TYPE_NAMESPACE_PREFIXES

if TYPE_CHECKING:
    from .model import AbiSnapshot, Function, RecordType, Variable

__all__: list[str] = []

# libc++ (and Android NDK's libc++) wrap the whole standard library in an
# inline namespace directly under ``std::`` -- ``std::__1::vector<int>``,
# ``std::__ndk1::vector<int>`` -- invisible to normal C++ code (inline
# namespaces are transparent to lookup/spelling) but very much present in a
# debug-info-derived RecordType's own qualified name. A signature spelled by
# the same backend's bare-name convention omits it too, same as the
# "std::"-namespace prefix itself (Codex review, fresh evidence). libstdc++
# does the identical thing for its own post-C++11 dual-ABI types
# (``std::__cxx11::basic_string``/``list``, gated by
# ``_GLIBCXX_USE_CXX11_ABI``) -- confirmed empirically via a real
# DWARF-dumped ``std::string`` parameter: ``RecordType.name`` is
# ``"std::__cxx11::basic_string<...>"`` while ``snapshot.typedefs["std::string"]``
# resolves to the bare ``"basic_string<...>"`` (no ``__cxx11::`` at all).
_STDLIB_ABI_NAMESPACE_MARKERS: tuple[str, ...] = ("__1::", "__ndk1::", "__cxx11::")

# Boundary character class shared by type_string_references_name's manual
# check and the compiled multi-spelling pattern below -- kept as one
# constant so the two implementations can't silently drift apart.
_BOUNDARY_CHARS = "_:"

# Provenance origins that are confidently NOT part of the public header
# surface (same set as idioms.py's _NON_PUBLIC_ORIGINS, ADR-024/027) -- a
# function retained from one of these headers is not a public reachability
# root even when Visibility.PUBLIC (linkage and origin are independent axes;
# Codex review). EXPORT_ONLY/UNKNOWN are deliberately not included here:
# EXPORT_ONLY means "exported, no header at all" rather than "confidently
# private", and UNKNOWN is the no-public-header-set default that must stay
# inclusive so this degrades to the pre-provenance behaviour.
_NON_PUBLIC_ORIGINS = frozenset(
    {ScopeOrigin.PRIVATE_HEADER, ScopeOrigin.SYSTEM_HEADER, ScopeOrigin.GENERATED}
)


def type_string_references_name(type_string: str, name: str) -> bool:
    """Whether *type_string* mentions *name* as a whole type token.

    A plain ``name in type_string`` substring check would false-positive on
    ``"std::string"`` appearing inside ``"std::stringstream"`` (a distinct
    type) or ``"xstd::string"`` (not even the same namespace); this requires
    non-identifier, non-``:``-scope characters (or the string boundary) on
    both sides of the match, so ``"const std::string &"`` matches
    ``"std::string"`` but ``"std::stringstream"`` does not.

    >>> type_string_references_name("const std::string &", "std::string")
    True
    >>> type_string_references_name("std::stringstream", "std::string")
    False
    >>> type_string_references_name("xstd::string", "std::string")
    False
    >>> type_string_references_name("std::vector<std::string>", "std::string")
    True
    >>> type_string_references_name("std::string", "std::string")
    True
    """
    start = 0
    while True:
        idx = type_string.find(name, start)
        if idx == -1:
            return False
        # A "" boundary (start/end of the whole string) is always a valid
        # token edge -- note that `"" in "_:"` is trivially True in Python
        # (the empty string is a substring of anything), so that check must
        # only run on an actual character, never on the "no character here"
        # sentinel, or a match at the very start/end of type_string would be
        # wrongly rejected.
        before = type_string[idx - 1] if idx > 0 else ""
        after_idx = idx + len(name)
        after = type_string[after_idx] if after_idx < len(type_string) else ""
        before_ok = before == "" or not (before.isalnum() or before in "_:")
        after_ok = after == "" or not (after.isalnum() or after in "_:")
        if before_ok and after_ok:
            return True
        start = idx + 1


def _record_identity(name: str, qualified_name: str | None) -> str:
    """The best available fully-qualified spelling for a record: the
    dedicated ``qualified_name`` field when the producer populated it
    (castxml, direct-clang), else ``name`` itself (DWARF, which has no
    separate field and instead bakes the namespace straight into ``name``;
    see ``RecordType.qualified_name``'s own docstring)."""
    return qualified_name or name


def _stripped_signature_spelling(identity: str) -> str | None:
    """The namespace-prefix-stripped spelling a real dumper backend's own
    ``Function.return_type``/``Param.type``/``TypeField.type`` strings
    actually use for *identity*, or ``None`` if *identity* carries no
    recognized stdlib namespace prefix at all.

    ``RecordType.name``/``qualified_name`` and the *signature* type-string
    fields are populated by independent code paths per backend and do not
    share one spelling convention: castxml's ``_type_name`` and the direct-
    clang backend's field/param types are built from the bare (unqualified)
    declaration name, while DWARF bakes the full namespace into ``name``
    directly. Empirically (verified against a real compiled+DWARF-dumped
    ``std::vector<int>`` parameter), the identity string
    ``"std::vector<int, std::allocator<int> >"`` never itself appears in a
    signature — only its namespace-prefix-stripped form
    ``"vector<int, std::allocator<int> >"`` does, because
    ``Function.return_type``/``Param.type`` spell the outermost type bare
    even when ``RecordType.name`` is fully qualified.

    libc++ (and Android NDK's libc++) additionally wrap the standard library
    in an inline namespace right after ``std::`` (``std::__1::vector<int>``,
    ``std::__ndk1::vector<int>``), and libstdc++ does the same for its own
    post-C++11 dual-ABI types (``std::__cxx11::basic_string``/``list``) —
    invisible to real C++ code but very much present in the debug-info-
    derived qualified name, so it must be stripped too or the reconstructed
    spelling (``"__1::vector<int>"``) still never matches a bare backend
    signature (Codex review, fresh evidence).

    Resolving a signature spelled with a typedef alias to a stdlib type
    (``std::string`` naming the real ``std::__cxx11::basic_string<...>``)
    is handled separately, in :func:`_typedef_spelling_targets` — this
    function only strips the namespace/ABI-tag *prefix* of an already-known
    identity, it does not resolve typedef aliasing on its own.
    """
    for prefix in STDLIB_TYPE_NAMESPACE_PREFIXES:
        if identity.startswith(prefix):
            rest = identity[len(prefix) :]
            for marker in _STDLIB_ABI_NAMESPACE_MARKERS:
                if rest.startswith(marker):
                    rest = rest[len(marker) :]
                    break
            return rest
    return None


def _bare_type_name(identity: str) -> str:
    """*identity* with its outer namespace-qualification fully stripped —
    the innermost suffix from :func:`_namespace_suffix_spellings` (the
    fully bare leaf), keeping any inner qualified template arguments
    intact. See that function's docstring for the full rationale.
    """
    return _namespace_suffix_spellings(identity)[-1]


def _non_stdlib_signature_spellings(
    non_stdlib_identities: frozenset[str],
) -> frozenset[str]:
    """Every spelling a real dumper backend could use in a signature to
    name *some* non-stdlib record: its full identity, plus every
    namespace-suffix spelling from :func:`_namespace_suffix_spellings`
    (not just the fully bare leaf).

    Deliberately broader than :func:`_spelling_index`'s own ``record_index``
    return value — an ambiguous suffix (shared by two or more distinct
    non-stdlib records) is still included here even though ``record_index``
    itself drops it (Codex review, fresh evidence): a non-stdlib record
    like ``api::vector<int>`` is spelled bare as ``"vector<int>"`` in a real
    signature, and a stdlib candidate like ``std::vector<int>`` can
    independently strip to that exact same bare spelling. Checking a
    stdlib stripped spelling only against *full* non-stdlib identities
    missed this collision entirely, since ``"vector<int>"`` never equals
    ``"api::vector<int>"`` — letting a signature naming the unrelated user
    type also mark the real ``std::vector<int>`` as directly referenced.
    Whether that suffix is ambiguous or not is irrelevant here: either way
    it is a real spelling *some* non-stdlib record can be named by, so a
    stdlib candidate reducing to it must still be rejected as a possible
    collision (same false-negative-over-false-positive principle as every
    other collision guard in this module).
    """
    spellings: set[str] = set()
    for identity in non_stdlib_identities:
        spellings.update(_namespace_suffix_spellings(identity))
    return frozenset(spellings)


def _spelling_index(
    stdlib_identities: list[str],
    non_stdlib_identities: frozenset[str],
    enum_identities: frozenset[str] = frozenset(),
    typedef_spelling_targets: dict[str, frozenset[str]] | None = None,
) -> tuple[dict[str, frozenset[str]], dict[str, frozenset[str]]]:
    """Returns ``(stdlib_index, record_index)`` — separate spelling ->
    {identity, ...} maps for stdlib candidates (the ultimate targets) and
    non-stdlib records (intermediate reachability-closure nodes — see
    :func:`directly_referenced_stdlib_types`'s worklist).

    Kept as two independent indices, scanned via two independently
    compiled patterns rather than one combined pattern (Codex review,
    fresh evidence): a non-stdlib record's own identity can itself embed a
    stdlib type's spelling verbatim, e.g. a registered non-stdlib record
    identity ``"Wrapper<std::string>"`` naming a public function's
    parameter type exactly. A single non-overlapping ``finditer()`` pass
    over one combined pattern tries the longest alternative first, matches
    the *whole* ``"Wrapper<std::string>"`` span as one non-stdlib hit, and
    then — because regex matches don't overlap — never independently
    notices the nested ``"std::string"`` substring inside that same span,
    even though it is directly present in the public signature text.
    Scanning with two separate compiled patterns means the stdlib pass
    finds ``"std::string"`` regardless of what the non-stdlib pass matched
    in the same text.

    A stdlib candidate's stripped spelling that collides with a real,
    unrelated non-stdlib record's own identity *or bare alias* (see
    :func:`_non_stdlib_signature_spellings`) is dropped (Codex review,
    fresh evidence): a library can happen to define its own public type
    with the exact bare spelling a stdlib candidate reduces to after
    stripping (e.g. its own top-level ``vector<int, ...>``, or a
    namespace-qualified ``api::vector<int>`` whose *bare* signature
    spelling is the same ``"vector<int>"``), and a signature naming that
    unrelated user type must not be misread as a direct stdlib reference —
    silently missing that stdlib candidate here (a false negative) is far
    safer than attributing an unrelated type's layout change to it (a
    false positive). Multiple *stdlib* identities can legitimately share
    one spelling (e.g. two distinct namespaces both reducing to the same
    bare form) — every one of them is recorded, not just the first.

    A non-stdlib record's own namespace-suffix spelling (see
    :func:`_namespace_suffix_spellings` — every suffix obtainable by
    dropping some prefix of its scope chain, not just the fully bare
    leaf) is dropped instead of recorded when it is ambiguous — shared by
    two or more *different* non-stdlib records (Codex review, fresh
    evidence: e.g. ``api::Inner`` and ``detail::Inner`` both reducing to
    bare ``Inner``): unlike the stdlib case above, queuing every colliding
    record here would let a signature naming one of them wrongly walk an
    unrelated internal record's fields too, misattributing its own
    implementation-only churn as publicly reachable. Each record's own
    full identity is never ambiguous this way and is always kept.

    *enum_identities* (default empty) is the same kind of collision guard,
    but against ``snapshot.enums`` rather than another record (Codex
    review, fresh evidence): a bare spelling shared by a record and an
    unrelated enum (e.g. ``mine::Wrapper`` and ``other::Wrapper``, both
    spelled bare ``"Wrapper"``) previously resolved unambiguously to the
    record here, since this index never knew enums existed at all --
    letting a signature that could just as well have meant the enum walk
    the record's own fields with full, *direct* (``via_typedef=False``)
    trust, bypassing every enum-aware ambiguity check this module applies
    everywhere else (those all live *outside* the scan, in
    :func:`directly_referenced_stdlib_type_spellings`, which cannot rescue
    a record whose reachability itself -- not just its stripped spelling --
    was never proven). Never adds an enum as a matchable ``record_index``
    entry itself (an enum has no fields to walk, and
    :func:`_walk_reached_records`'s ``non_stdlib_records[identity]`` lookup
    would raise ``KeyError`` for one) -- purely a *collision* input,
    mirroring how :func:`_non_stdlib_signature_spellings` is reused for the
    identical purpose everywhere else in this module.

    *typedef_spelling_targets* (default ``None``; see
    :func:`_raw_typedef_spellings`) is the same kind of collision guard
    against ``snapshot.typedefs`` (Codex review, fresh evidence): this index
    was built entirely independently of the typedef vocabulary, so a
    record's spelling could unconditionally shadow a typedef alias reducing
    to the identical spelling -- ``other::Wrapper -> Other`` alongside a
    global record ``Wrapper``, both bare ``"Wrapper"``, previously always
    resolved to the record even though :func:`_typedef_spelling_targets`
    independently drops its own candidate for the identical collision
    (checked one-directionally, against records only). Deliberately the
    *raw* candidate vocabulary (:func:`_raw_typedef_spellings`), not the
    resolved :func:`_typedef_spelling_targets` index or the already-non-
    stdlib-filtered :func:`_typedef_candidate_spellings` -- both of those
    already assume a record collision means the record wins, which is
    exactly the one-directional gap being closed here.

    A colliding spelling is dropped only when some typedef's target names
    something *other* than the record identity being checked (Codex review,
    fresh evidence, a second round): a typedef spelling colliding with a
    record but *resolving to that same record* (``other::Wrapper ->
    Wrapper``) is not a real ambiguity -- both routes name the identical
    entity, so keeping the record here is correct and required, not merely
    safe, the same "target genuinely names the very identity being
    evaluated" exception :func:`directly_referenced_stdlib_type_spellings`
    already applies for the analogous stdlib-identity case.
    """
    non_stdlib_spellings = _non_stdlib_signature_spellings(non_stdlib_identities)
    stdlib_index: dict[str, set[str]] = {}
    for identity in stdlib_identities:
        stdlib_index.setdefault(identity, set()).add(identity)
        stripped = _stripped_signature_spelling(identity)
        if stripped is not None and stripped not in non_stdlib_spellings:
            stdlib_index.setdefault(stripped, set()).add(identity)

    enum_bare_spellings = (
        _non_stdlib_signature_spellings(enum_identities)
        if enum_identities
        else frozenset()
    )
    typedef_spelling_targets = typedef_spelling_targets or {}

    def _typedef_collides(bare: str, identity: str) -> bool:
        targets = typedef_spelling_targets.get(bare)
        return targets is not None and targets != {identity}

    record_index: dict[str, set[str]] = {}
    generic_bare: dict[str, set[str]] = {}
    for identity in non_stdlib_identities:
        # A record whose own full identity carries no namespace/class
        # qualification at all (a global record) is spelled bare by
        # construction -- structurally indistinguishable from a *derived*
        # suffix an enum or typedef could ALSO reduce to, so it needs the
        # identical collision check the derived-suffix loop below already
        # applies, not the "a record's own full identity is never
        # ambiguous" exemption that holds for a genuinely qualified one
        # (Codex review, fresh evidence: a global record `Wrapper` and an
        # enum `other::Wrapper`, or a typedef `other::Wrapper -> Other`,
        # both reducing to bare `"Wrapper"`, previously let the record win
        # by default since this registration ran unconditionally for every
        # non-stdlib identity's own full form, never checked against either
        # collision vocabulary the way a *derived* suffix already was).
        if identity not in enum_bare_spellings and not _typedef_collides(
            identity, identity
        ):
            record_index.setdefault(identity, set()).add(identity)
        for suffix in _namespace_suffix_spellings(identity)[1:]:
            generic_bare.setdefault(suffix, set()).add(identity)
    for bare, ids in generic_bare.items():
        if (
            bare in non_stdlib_identities
            or bare in enum_bare_spellings
            or (len(ids) == 1 and _typedef_collides(bare, next(iter(ids))))
        ):
            # A derived suffix that collides with a *different* record's own
            # full identity (Codex review, fresh evidence: identities "Inner"
            # and "api::Inner" both present), with an unrelated enum's own
            # spelling (a record and an enum both reducing to bare
            # "Wrapper"), or with a spelling some typedef key or its own
            # derived suffix could also produce for a *different* target
            # than this record (a record `Wrapper` and a typedef
            # `other::Wrapper -> Other`, both reducing to bare "Wrapper" --
            # `_typedef_spelling_targets` already excludes its own candidate
            # for this same collision, so treating the record side
            # identically means neither interpretation wins by default; a
            # typedef resolving to *this same record* instead, e.g.
            # `other::Wrapper -> Wrapper`, is not a collision at all --
            # `_typedef_collides` only fires when genuinely ambiguous), is
            # ambiguous the same way two colliding derived suffixes are.
            # Removing the spelling entirely
            # (not just refusing to add the *other* record's candidates) is
            # required, not merely safe (Codex review, fresh evidence):
            # direct-clang's own "drop the enclosing namespace" convention
            # (see _namespace_suffix_spellings) means a signature declared
            # *inside* namespace api can spell api::Inner bare as "Inner"
            # too, so leaving record_index["Inner"] pointing at the
            # unrelated global Inner would misattribute that signature to
            # the wrong record instead of leaving it correctly unresolved.
            record_index.pop(bare, None)
            continue
        if len(ids) == 1:
            record_index.setdefault(bare, set()).update(ids)
        # else: ambiguous suffix spelling shared by distinct records -- drop.

    return (
        {spelling: frozenset(ids) for spelling, ids in stdlib_index.items()},
        {spelling: frozenset(ids) for spelling, ids in record_index.items()},
    )


def _typedef_spelling_targets(
    typedefs: dict[str, str], non_stdlib_identities: frozenset[str]
) -> dict[str, str]:
    """spelling -> target type string, for every ``snapshot.typedefs`` key
    plus (for a stdlib-namespaced key) its namespace/ABI-tag-stripped bare
    form — closing the last piece of the typedef-aliased-stdlib-type gap
    (Codex review, fresh evidence, verified against a real DWARF-dumped
    ``std::string`` parameter): ``snapshot.typedefs["std::string"]``
    resolves to the bare ``"basic_string<char, std::char_traits<char>,
    std::allocator<char> >"`` (matching the real ``RecordType``'s identity
    after :func:`_stripped_signature_spelling`), but the typedef *key*
    itself is the fully-qualified ``"std::string"`` while the DWARF
    backend's own signature spelling is the bare ``"string"`` — the exact
    same bare-vs-qualified split ``_spelling_index`` already handles for
    ``RecordType`` identities, just one level up, on the alias name itself.

    A stripped key that collides with a real non-stdlib record's own
    identity *or bare alias* (see :func:`_non_stdlib_signature_spellings`),
    or with a *different* typedef's target, is dropped rather than
    recorded (same false-negative-over-false-positive principle as
    ``_spelling_index``): an ambiguous resolution is worse than none.
    Checking only full non-stdlib identities missed a real collision
    (Codex review, fresh evidence): a non-stdlib record like ``api::string``
    is spelled bare as ``"string"`` in a real signature, the same bare
    spelling the ``"std::string"`` typedef key strips to — without also
    checking bare aliases, a signature naming the unrelated user type would
    incorrectly resolve through this typedef target to ``std::string``'s
    real backing record, unfiltering stdlib layout churn that isn't real.

    A **non-stdlib** namespace-qualified typedef key also needs suffix
    spellings, not just a stdlib-stripped one (Codex review, fresh
    evidence): the DWARF backend stores a qualified key like
    ``"api::Alias"`` while a declaration's own type string spells the bare
    ``"Alias"`` — the exact same bare-vs-qualified split as everywhere
    else in this module, just on the typedef key this time instead of a
    ``RecordType`` identity. Without this, a public signature using the
    bare alias never resolves through this typedef target at all, silently
    missing a stdlib field reachable through it. Reuses
    :func:`_namespace_suffix_spellings` (every suffix, not just the fully
    bare leaf — already correct for qualified template arguments and for
    a partially-qualified nested-class spelling) rather than a separate
    stdlib-only stripper, since this case has nothing to do with stdlib
    namespace/ABI markers.

    An *exact* typedef key is tracked through the same ambiguity-counting
    structure as every derived suffix, not given automatic priority over
    one (Codex review, fresh evidence): when ``snapshot.typedefs`` holds
    both a global ``"Alias" -> "std::…"`` and a qualified
    ``"api::Alias" -> "Foo"``, a declaration inside ``api`` can
    legitimately spell the latter as bare ``"Alias"`` too — the bare
    spelling is genuinely ambiguous between the two real typedefs, and
    silently preferring the pre-existing exact key (as an earlier version
    did merely because it already had an entry) could resolve a bare
    ``"Alias"`` to the *wrong* one of the two, either hiding a real
    non-stdlib reference or fabricating a stdlib one. Both contribute to
    the same per-spelling target set; a spelling resolves only when every
    contributing source agrees on exactly one target.

    An *exact* key is also checked against ``non_stdlib_spellings`` before
    being registered at all, the same guard every derived candidate already
    goes through (Codex review, fresh evidence): the direct-clang backend's
    own typedef-scope-loss (a namespaced ``namespace api { using Alias =
    std::string; }`` is stored under the bare key ``"Alias"``, losing
    ``api::``) can make an exact typedef key collide with an unrelated
    non-stdlib record's own signature spelling — e.g. a global ``struct
    Alias {};`` sharing the identical bare name. Registering the exact key
    unconditionally let a public function taking that unrelated ``Alias``
    record by value resolve through the typedef target instead of the real
    record, incorrectly marking the typedef's stdlib target (e.g.
    ``std::string``) reachable. Skipping the exact key's registration
    entirely when it collides (rather than letting it compete for
    ambiguity resolution) matches how a colliding *derived* candidate is
    already handled: the spelling belongs to the real record, not to this
    typedef, so the typedef contributes nothing for it.
    """
    non_stdlib_spellings = _non_stdlib_signature_spellings(non_stdlib_identities)
    targets_by_spelling: dict[str, set[str]] = {}
    for key, target in typedefs.items():
        if key not in non_stdlib_spellings:
            targets_by_spelling.setdefault(key, set()).add(target)
        candidates = {
            c
            for c in (
                _stripped_signature_spelling(key),
                *_namespace_suffix_spellings(key),
            )
            if c is not None and c != key
        }
        for stripped in candidates:
            if stripped in non_stdlib_spellings:
                continue
            targets_by_spelling.setdefault(stripped, set()).add(target)
    index: dict[str, str] = {}
    for spelling, targets in targets_by_spelling.items():
        if len(targets) == 1:
            index[spelling] = next(iter(targets))
        # else: ambiguous (an exact key and a derived suffix disagreeing,
        # a stripped/suffix spelling colliding with a real record, or two
        # typedefs disagreeing on the target) -- drop.
    return index


def _typedef_candidate_spellings(
    typedefs: dict[str, str], non_stdlib_identities: frozenset[str]
) -> frozenset[str]:
    """Every spelling *some* typedef in *typedefs* could be reached by --
    its own key or a derived namespace-suffix/stdlib-stripped form -- with
    no regard to whether :func:`_typedef_spelling_targets` can resolve it
    to one unambiguous target.

    A caller wanting to know "does the typedef vocabulary reach this
    spelling at all" cannot use :func:`_typedef_spelling_targets`'s own
    resolved index for that: an ambiguous spelling (two typedefs
    disagreeing on the target, or a derived suffix disagreeing with its own
    exact key) is deliberately *dropped* from that index rather than
    resolved to either candidate, since neither one is safe to act on. But
    "dropped because ambiguous" and "never reached by any typedef at all"
    are different facts a consumer needs to tell apart -- a stripped stdlib
    spelling colliding with an ambiguous typedef spelling is exactly as
    untrustworthy as colliding with a resolved, disagreeing one; treating
    the former as "no collision" (e.g.
    ``typedefs={"exception": "mine::Thing", "mine::exception": "mine::Other"}``
    makes ``"exception"`` ambiguous and therefore absent from the resolved
    index, silently letting an unrelated ``std::exception`` through) is the
    gap this function closes. Mirrors the same key/candidate-derivation
    logic :func:`_typedef_spelling_targets` uses (kept as a parallel
    derivation rather than a shared generator, to minimize churn against
    that function's own already-verified body).
    """
    non_stdlib_spellings = _non_stdlib_signature_spellings(non_stdlib_identities)
    candidates: set[str] = set()
    for key in typedefs:
        if key not in non_stdlib_spellings:
            candidates.add(key)
        for form in (
            _stripped_signature_spelling(key),
            *_namespace_suffix_spellings(key),
        ):
            if form is not None and form != key and form not in non_stdlib_spellings:
                candidates.add(form)
    return frozenset(candidates)


def _raw_typedef_spellings(typedefs: dict[str, str]) -> dict[str, frozenset[str]]:
    """spelling -> every raw target *some* typedef key could produce that
    spelling for -- own key, stdlib-stripped form, and every namespace-
    suffix form -- with **no** filtering against any other vocabulary at
    all (Codex review, fresh evidence).

    Deliberately distinct from both :func:`_typedef_spelling_targets` (which
    resolves a spelling to a *single* target, dropping the spelling
    entirely the moment it's ambiguous) and :func:`_typedef_candidate_spellings`
    (which already excludes a spelling colliding with ``non_stdlib_identities``,
    since that function exists to check whether a *stdlib* candidate's
    stripped form is safe against the typedef vocabulary, and a record
    collision already means "not safe" there): a caller checking a
    *record's own* spelling against the typedef vocabulary needs both the
    raw candidate set (unfiltered by any assumption about which side wins a
    collision) *and* the raw target(s), not just a yes/no membership test
    (Codex review, fresh evidence, a second round beyond the raw-membership
    version this function used to be): a typedef spelling that collides
    with a record but *resolves to that same record*
    (``other::Wrapper -> Wrapper``, ``typedefs={"other::Wrapper":
    "Wrapper"}``) is not a real ambiguity at all -- both routes name the
    identical entity -- and a caller must be able to tell that case apart
    from a spelling whose typedef target names something genuinely
    different, the same "target genuinely names the very identity being
    evaluated" exception :func:`directly_referenced_stdlib_type_spellings`
    already applies for the analogous stdlib-identity case.
    """
    targets: dict[str, set[str]] = {}
    for key, target in typedefs.items():
        targets.setdefault(key, set()).add(target)
        for form in (
            _stripped_signature_spelling(key),
            *_namespace_suffix_spellings(key),
        ):
            if form is not None:
                targets.setdefault(form, set()).add(target)
    return {spelling: frozenset(ts) for spelling, ts in targets.items()}


def _compile_spelling_pattern(spellings: Collection[str]) -> re.Pattern[str] | None:
    """One compiled alternation matching any of *spellings* as a whole type
    token — the same boundary semantics as :func:`type_string_references_name`
    (non-identifier, non-``:``-scope character, or the string boundary, on
    both sides), but resolved in a single pass over each declaration's type
    string regardless of how many spellings there are.

    This is the fix for the quadratic candidate-by-candidate scan (Codex
    review, fresh evidence: a synthetic snapshot with 1,000 functions and
    1,000 unreferenced stdlib records took over a second in a single
    ``directly_referenced_stdlib_types`` call, and nine independent
    ``diff_types.py`` call sites each repeated it) — building one pattern
    once turns the scan from O(candidates × declarations) into
    O(declarations), independent of candidate count. Longest-first ordering
    doesn't change *whether* something matches (every alternative is
    anchored to the same boundary, so a shorter spelling can't "shadow" a
    longer one the way it could in an unanchored first-match scan) but keeps
    the compiled pattern's alternation order deterministic for a stable
    ``.finditer()`` iteration order.
    """
    if not spellings:
        return None
    ordered = sorted(spellings, key=len, reverse=True)
    alternation = "|".join(re.escape(s) for s in ordered)
    return re.compile(
        rf"(?<![A-Za-z0-9{_BOUNDARY_CHARS}])(?:{alternation})(?![A-Za-z0-9{_BOUNDARY_CHARS}])"
    )


def _finditer_allow_nested(
    pattern: re.Pattern[str], text: str, start: int = 0, end: int | None = None
) -> list[re.Match[str]]:
    """Every match of *pattern* in ``text[start:end]``, including one nested
    strictly inside another match's own span (Codex review, fresh evidence):
    plain ``.finditer()`` only returns *non-overlapping* matches, continuing
    its search from the end of each match — so when one candidate's spelling
    is a substring of another's own registered spelling (e.g. ``"std::string"``
    inside ``"std::vector<std::string>"``, or a non-stdlib ``"Inner"`` inside
    ``"Wrapper<Inner>"``), the alternation's longest-first ordering matches
    the *outer* candidate first, consuming the whole span, and the inner one
    is never independently reported even though it is directly present in
    the signature text. Splitting stdlib vs. non-stdlib into two independent
    patterns (an earlier fix) only solved *cross*-index masking — two
    candidates from the *same* index (both stdlib, or both non-stdlib
    records) can still mask each other this way.

    Uses an explicit stack rather than recursing into ``text[m.start() + 1 :
    m.end()]`` for every match found (Codex review, fresh evidence): a
    genuinely deep chain of registered spellings each nested one inside the
    next — plausible for template-metaprogramming-heavy C++ under a
    compiler's configured ``-ftemplate-depth`` (GCC/Clang both default well
    into the hundreds, and it's routinely raised higher) — previously
    recursed one Python call per nesting level. Confirmed empirically: 1,000
    successively nested registered candidate spellings raised
    ``RecursionError`` under Python's default 1,000-frame recursion limit,
    aborting the whole comparison rather than degrading gracefully. An
    explicit stack has no such limit — each entry is still a strictly
    narrower window than the match that produced it, so the search still
    always terminates, just without consuming Python's call stack to do it.
    """
    if end is None:
        end = len(text)
    matches: list[re.Match[str]] = []
    stack: list[tuple[int, int]] = [(start, end)]
    while stack:
        window_start, window_end = stack.pop()
        for m in pattern.finditer(text, window_start, window_end):
            matches.append(m)
            if m.end() - m.start() > 1:
                stack.append((m.start() + 1, m.end()))
    return matches


def _partition_snapshot_types(
    snapshot: AbiSnapshot,
) -> tuple[list[str], frozenset[str], dict[str, list[RecordType]]]:
    """Split ``snapshot.types`` into the stdlib candidates and everything else.

    Returns the stdlib identities (what the scan is looking *for*), the
    non-stdlib identities (what a signature may legitimately name on the way
    there), and the non-stdlib records keyed by identity.

    That last one is a ``list`` per identity, not a single record, deliberately
    (Codex review, fresh evidence): ``snapshot.types`` can carry several entries
    sharing one identity (a complete definition alongside an ODR-duplicate or an
    incomplete declaration), and a plain dict would silently drop all but the
    last — so a public signature reaching that identity would walk only the
    survivor, missing a ``std::`` field the other entry carries. ``surface.py``'s
    own ``record_by_name`` index is a list per identity for exactly this reason.
    """
    stdlib_identities: list[str] = []
    non_stdlib_identities: set[str] = set()
    non_stdlib_records: dict[str, list[RecordType]] = {}
    for t in snapshot.types:
        identity = _record_identity(t.name, t.qualified_name)
        if identity.startswith(STDLIB_TYPE_NAMESPACE_PREFIXES):
            stdlib_identities.append(identity)
        else:
            non_stdlib_identities.add(identity)
            non_stdlib_records.setdefault(identity, []).append(t)
    return stdlib_identities, frozenset(non_stdlib_identities), non_stdlib_records


def _is_public_non_stdlib_declaration(
    decl: Function | Variable,
    *,
    exclude_export_only: bool = False,
    committed_roots: frozenset[str] | None = None,
) -> bool:
    """True when *decl* may seed the scan as a public reachability root.

    Four independent reasons a retained declaration must not be treated as a
    public reachability root, applied identically to both kinds — see
    :func:`directly_referenced_stdlib_types`'s own docstring for why each one
    matters:

    * its display name is stdlib-namespaced (unconditionally -- this module
      has no ``library``-aware exception the way
      :func:`abicheck.model.stdlib_namespaces_excluded` does for a real
      libstdc++/libc++ self-comparison, where ``std::`` is the actual
      surface under test rather than a dependency. A real fix needs a
      ``library``-aware notion of "root" flowing consistently through both
      this check and :func:`_partition_snapshot_types`'s whole
      stdlib/non-stdlib partition -- its own scoped design, not a
      drive-by extension of this check. A genuine finding on such a
      comparison degrades to this module's own already-conservative
      default: ``UNKNOWN_UNRESOLVED``, never a false positive);
    * its ``visibility`` is not ``PUBLIC`` (``HIDDEN``/``ELF_ONLY``);
    * its ``origin`` is a confidently-non-public header
      (``PRIVATE_HEADER``/``SYSTEM_HEADER``/``GENERATED``) — linkage and origin
      are independent axes (ADR-024 D1);
    * its *recovered* qualified name (from ``mangled``, Itanium or MSVC) is
      stdlib-namespaced, which is the only check that catches a stdlib-internal
      declaration whose backend recorded the display name bare.

    ``exclude_export_only``, when set, additionally rejects a declaration
    whose ``origin`` is ``ScopeOrigin.EXPORT_ONLY`` (Codex review, fresh
    evidence): the default (``False``, this module's own general-purpose ABI-
    surface use in ``diff_types.py``) deliberately keeps such a declaration,
    since there ``directly_referenced_stdlib_types`` only answers "is this
    stdlib type ABI-reachable at all", evidence-tier-agnostic by design. A
    caller building evidence specifically scoped to the *public-header*
    contract domain (``contract_pipeline.py``) must not let a declaration
    that exists only because the binary exports it — with no header backing
    it at all — stand in for public-header evidence: that is precisely the
    boundary the ``exports`` domain exists to evaluate separately, and
    conflating the two let an export-only stdlib reference confirm a
    ``--contract public`` finding it has no bearing on.

    ``committed_roots`` (default ``None``, meaning "no manifest active")
    mirrors ``PipelineContext.public_surface_allowlist`` (Codex review,
    fresh evidence): a ``compare --post-manifest`` run scopes the public
    contract down to an *explicit* committed-symbol allowlist, demoting any
    export finding whose symbol isn't in it — but this scan's own seeding
    never consulted that allowlist at all, so a still-``PUBLIC``,
    header-committed but *uncommitted-by-manifest* function's signature
    could still seed a stdlib direct-reference root and confirm an
    unrelated dependency-layout finding as ``IN_CONTRACT``/``COMPLETE``,
    even though no committed export references that type. When set, a
    declaration seeds the scan if its ``mangled``, its display ``name``, or
    its mangled-recovered *qualified* name is a member — matching
    :func:`abicheck.post_manifest.contract_scope_allowlist`'s own two-key
    membership convention (its ``_add`` helper records both) plus a third:
    ``--public-symbol``/``force_public_symbols`` explicitly accepts a
    "mangled or demangled name" (Codex review, fresh evidence), and a
    namespaced demangled value (``ns::api``) matches neither the linker
    ``mangled`` name nor a bare, unqualified backend spelling of ``name``
    (castxml/direct-clang's own convention) — only the recovered qualified
    name does.
    """
    if decl.name.startswith(STDLIB_TYPE_NAMESPACE_PREFIXES):
        return False
    if decl.visibility != Visibility.PUBLIC:
        return False
    if decl.origin in _NON_PUBLIC_ORIGINS:
        return False
    if exclude_export_only and decl.origin is ScopeOrigin.EXPORT_ONLY:
        return False
    qualified = itanium_qualified_name(decl.mangled) or msvc_qualified_name(
        decl.mangled
    )
    if committed_roots is not None and not (
        decl.mangled in committed_roots
        or decl.name in committed_roots
        or (qualified is not None and qualified in committed_roots)
    ):
        # `--public-symbol`/`force_public_symbols` explicitly accepts a
        # "mangled or demangled name" (Codex review, fresh evidence, follow-
        # up to the forced-public widening fix above): a user forcing a
        # namespaced symbol spells it `ns::api`, which never appears as
        # either `decl.mangled` (the linker name) or `decl.name` (a bare,
        # unqualified backend spelling for castxml/direct-clang -- see this
        # module's own spelling-convention docs elsewhere) -- so a forced
        # namespaced root silently failed to seed here even though the
        # identical bare-name case (`--public-symbol api`) already worked.
        # `qualified` (the same mangled-recovered qualified name the stdlib-
        # prefix check below already computes) is exactly the spelling a
        # demangled `--public-symbol` value matches against.
        return False
    return not (
        qualified is not None and qualified.startswith(STDLIB_TYPE_NAMESPACE_PREFIXES)
    )
