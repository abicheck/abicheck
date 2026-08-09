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

"""Direct-reference reachability for standard-library/runtime-namespaced types
(status-review item 3: "direct vs transitive type reachability").

Every ``diff_*`` module that filters out ``std::``/``__gnu_cxx::``/etc. types
(:func:`abicheck.name_classification.is_non_abi_surface_type`) does so purely
by matching a type's own *name* against
:data:`abicheck.name_classification.STDLIB_TYPE_NAMESPACE_PREFIXES` — this is
correct for the common case (a stdlib type reached only through deep
template-instantiation internals, e.g. ``std::_Rb_tree_node_base`` or
``std::string::_Alloc_hider``, is real toolchain-artifact churn, not the
library's own ABI surface) but treats that identically to a stdlib type used
**directly** in a public function's own signature (e.g. ``void
foo(std::string s)``) or as a public (non-stdlib) type's own field — a case
where the library's ABI genuinely does depend on that stdlib type's layout,
and blanket-filtering it can hide a real, consumer-visible break (e.g. a
libstdc++ dual-ABI flip affecting every public function taking ``std::string``
by value).

This module computes, from an :class:`abicheck.model.AbiSnapshot` alone (no
build/source integration needed), which stdlib-namespaced type names are
*directly* referenced by a non-stdlib declaration's own signature — i.e.
reachable at distance one from the public surface, as opposed to only
reachable transitively via deep instantiation chains never named anywhere
outside the standard library itself.

**Wired into `diff_types.py`'s ``RecordType``-based detectors** (struct/
union size, alignment, fields, bases, vtable, kind, reserved fields,
qualifiers, renames, deprecation) via the shared ``_is_abi_surface_type``
gate. The remaining ~14 ``is_non_abi_surface_type``/``is_abi_surface_type_name``
call sites across ``diff_platform.py``/``diff_symbols.py``/
``diff_vtable_layout.py``/``diff_stdlib_impl.py``/``diff_layout.py``/
``diff_filtering.py``/``diff_type_spellings.py``, plus ``diff_types.py``'s
own enum/typedef paths, remain unwired — each needs its own site
individually verified against the FP-rate/mutation-score gates (this
codebase's test-quality guards exist specifically to catch exactly this
kind of change going wrong), a scoped follow-up rather than a drive-by
extension here.

**Known gap, deliberately not attempted here (Codex review, fresh
evidence): this module has no notion of "the library under comparison IS
the C++ runtime itself" the way :func:`abicheck.model.
stdlib_namespaces_excluded` does.** That function flips OFF the blanket
``std::``-filtering elsewhere in the pipeline when either side's
``library``/ELF SONAME names libstdc++/libc++ (via
:func:`abicheck.name_classification.is_cxx_runtime_library`), since for
those libraries ``std::`` is the actual surface under test, not a
dependency. This module's every declaration-seeding check
(:func:`_is_public_non_stdlib_declaration`) and its whole stdlib/non-stdlib
partition (:func:`_partition_snapshot_types`) instead hard-code the
opposite premise throughout — a ``std::``-namespaced declaration can never
seed the scan, and every ``std::``-namespaced ``RecordType`` is
unconditionally a *target* to search for, never a candidate *root*. For a
real libstdc++/libc++ comparison this inverts: confirmed empirically that a
public ``std::api(vector<int>)``-shaped declaration is rejected as a seed
by :func:`_is_public_non_stdlib_declaration`'s first check
(``decl.name.startswith(STDLIB_TYPE_NAMESPACE_PREFIXES)``) regardless of
which library is being compared, so this module's direct-reference proof
never fires for that pairing. A real fix is not a one-line threshold
change at that single check site: it needs a `library`-aware notion of
"root" flowing through both the seeding check *and* the stdlib/non-stdlib
partition consistently (a `std::`-owned declaration would need to become a
valid *root*, and reasoning about "direct reference to a stdlib
dependency" stops meaning anything once the dependency and the library are
the same thing) — its own scoped design matching
``stdlib_namespaces_excluded``'s existing library-detection convention,
not a drive-by extension of one check. Until then, a libstdc++/libc++
self-comparison under ``contract=public`` degrades exactly like every
other unresolvable case this module documents throughout: a genuine
template-layout finding on such a comparison falls back to
``UNKNOWN_UNRESOLVED`` rather than confirming ``IN_CONTRACT`` — a
false-negative under this module's own already-stated conservative
default, never a false positive.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from typing import TYPE_CHECKING

from .diff_cxx_rules import itanium_qualified_name, msvc_qualified_name, owner_class_of
from .model import ScopeOrigin, Visibility
from .name_classification import STDLIB_TYPE_NAMESPACE_PREFIXES

if TYPE_CHECKING:
    from .model import AbiSnapshot, Function, RecordType, Variable

__all__ = [
    "directly_referenced_stdlib_type_spellings",
    "directly_referenced_stdlib_types",
    "type_string_references_name",
]

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


def _namespace_suffix_spellings(identity: str) -> list[str]:
    """Every suffix spelling of *identity* obtainable by dropping some
    prefix of its namespace/class-scope chain, at each ``"::"`` boundary
    that occurs at template-argument bracket depth zero — from the full
    identity itself (dropping nothing) down to the fully bare leaf.

    A real backend does not always spell a nested type as either the
    fully-qualified identity or the fully-bare leaf (Codex review, fresh
    evidence, confirmed empirically via ``clang -ast-dump`` on
    ``namespace api { struct Outer { struct Inner {}; }; Outer::Inner
    g(); }``): direct-clang prints that function's return type as exactly
    ``"Outer::Inner"`` — dropping the *enclosing namespace* (``api::``,
    implied by lookup context inside that namespace) while keeping the
    *class-nesting* qualifier (``Outer::``, a distinct scope that is never
    elided) — a partial qualification distinct from both the full identity
    ``"api::Outer::Inner"`` and the fully-bare leaf ``"Inner"``. Generating
    every such suffix (not just the two extremes) is what lets a signature
    spelled this way still resolve to the right record.

    A plain ``identity.rsplit("::", 1)`` would additionally split *inside*
    a template argument's own qualified name: for
    ``"api::Wrapper<dep::Tag>"``, the lexically last ``"::"`` belongs to
    the template argument ``dep::Tag``, not an outer namespace boundary.
    Tracking ``<``/``>`` nesting depth and only considering a ``"::"`` at
    depth zero as a namespace separator avoids that.

    Returns ``[identity]`` (a single-element list) when *identity* carries
    no depth-zero ``"::"`` at all (already bare, or only qualified inside
    template arguments).
    """
    depth = 0
    splits = [0]
    i = 0
    n = len(identity)
    while i < n:
        ch = identity[i]
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        elif ch == ":" and depth == 0 and i + 1 < n and identity[i + 1] == ":":
            splits.append(i + 2)
            i += 1
        i += 1
    return [identity[s:] for s in splits]


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
    record_index: dict[str, set[str]] = {}
    generic_bare: dict[str, set[str]] = {}
    for identity in non_stdlib_identities:
        record_index.setdefault(identity, set()).add(identity)
        for suffix in _namespace_suffix_spellings(identity)[1:]:
            generic_bare.setdefault(suffix, set()).add(identity)
    for bare, ids in generic_bare.items():
        if bare in non_stdlib_identities or bare in enum_bare_spellings:
            # A derived suffix that collides with a *different* record's own
            # full identity (Codex review, fresh evidence: identities "Inner"
            # and "api::Inner" both present), or with an unrelated enum's own
            # spelling (a record and an enum both reducing to bare
            # "Wrapper"), is ambiguous the same way two colliding derived
            # suffixes are. Removing the spelling entirely
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
    declaration seeds the scan only if either its ``mangled`` or its
    display ``name`` is a member — matching
    :func:`abicheck.post_manifest.contract_scope_allowlist`'s own two-key
    membership convention (its ``_add`` helper records both), since a
    manifest or export-table entry may name either spelling depending on
    producer/platform.
    """
    if decl.name.startswith(STDLIB_TYPE_NAMESPACE_PREFIXES):
        return False
    if decl.visibility != Visibility.PUBLIC:
        return False
    if decl.origin in _NON_PUBLIC_ORIGINS:
        return False
    if exclude_export_only and decl.origin is ScopeOrigin.EXPORT_ONLY:
        return False
    if committed_roots is not None and not (
        decl.mangled in committed_roots or decl.name in committed_roots
    ):
        return False
    qualified = itanium_qualified_name(decl.mangled) or msvc_qualified_name(
        decl.mangled
    )
    return not (
        qualified is not None and qualified.startswith(STDLIB_TYPE_NAMESPACE_PREFIXES)
    )


class _StdlibReferenceScan:
    """Mutable state of one :func:`directly_referenced_stdlib_types` walk.

    Owns the three compiled spelling patterns and the sets they feed: which
    stdlib identities are still unaccounted for, which have been referenced,
    which non-stdlib records have been reached, and which typedef aliases have
    already been followed. A class rather than a closure so the seeding pass and
    the record walk can share it as one explicit object.
    """

    def __init__(
        self,
        stdlib_identities: list[str],
        non_stdlib_identities: frozenset[str],
        typedefs: dict[str, str],
        enum_identities: frozenset[str] = frozenset(),
    ) -> None:
        self._stdlib_index, self._record_index = _spelling_index(
            stdlib_identities, non_stdlib_identities, enum_identities
        )
        stdlib_pattern = _compile_spelling_pattern(self._stdlib_index)
        # stdlib_index always has at least one entry here (every stdlib
        # identity maps at least itself), so _compile_spelling_pattern's
        # empty-input case never applies to this caller.
        assert stdlib_pattern is not None
        self._stdlib_pattern = stdlib_pattern
        self._record_pattern = _compile_spelling_pattern(self._record_index)
        self._typedef_targets = _typedef_spelling_targets(
            typedefs, non_stdlib_identities
        )
        self._typedef_pattern = (
            _compile_spelling_pattern(self._typedef_targets)
            if self._typedef_targets
            else None
        )
        self._referenced: set[str] = set()
        # An identity matched via its own literal, un-derived spelling
        # (``stdlib_index[identity] == {identity}``, its unconditional
        # self-key -- see :func:`_spelling_index`) -- as opposed to only
        # ever matched via a *derived* spelling (a stripped/bare form) that
        # may be shared with another stdlib identity or an unrelated
        # non-stdlib record/enum. Tracked separately from ``_referenced``
        # because a derived-spelling match's own ambiguity can only be
        # resolved by a consumer that knows *which* route actually proved
        # an identity -- this class is the only place that information
        # exists, since :func:`directly_referenced_stdlib_types`'s own
        # return value is a flat, routeless set.
        self._referenced_exact: set[str] = set()
        # An identity matched exactly, but only while recursively scanning a
        # typedef's *target* string (never in the declaration's own literal
        # text) -- keyed by identity, valued by every top-level alias
        # spelling that led to such a match. See :meth:`scan`'s own
        # ``via_typedef``/``origin_alias`` docstring for why this needs its
        # own bucket rather than folding straight into ``_referenced_exact``.
        self._exact_typedef_aliases: dict[str, set[str]] = {}
        # Broader siblings of the two sets above: trustworthy *at all* --
        # matched via *any* spelling (a derived/stripped form counts, not
        # only the identity's own self-key) in a route this scan considers
        # trustworthy (``_referenced_trusted``: found directly, via_typedef
        # False) or an alias-conditional route (``_trusted_via_alias``:
        # found only via a typedef alias, keyed the same way as
        # ``_exact_typedef_aliases``) (Codex review, fresh evidence).
        # ``directly_referenced_stdlib_type_spellings``'s own "this
        # identity's stripped form collides with nothing else in the
        # snapshot" shortcut previously trusted *any* member of
        # ``referenced()`` unconditionally for that check -- including one
        # matched only via a derived spelling inside a record's own field,
        # where the record itself was reached only through an ambiguous
        # typedef alias. The bare-spelling-uniqueness argument alone cannot
        # rescue that: "nothing else could this spelling mean" says nothing
        # about whether the signature legitimately reaches this spelling at
        # all. ``_referenced_exact``/``_exact_typedef_aliases`` stay exactly
        # as before (used by the *other*, "stripped form is ambiguous"
        # branch, which specifically needs the stronger self-key guarantee).
        self._referenced_trusted: set[str] = set()
        self._trusted_via_alias: dict[str, set[str]] = {}
        self._remaining = set(stdlib_identities)
        self._reached_records: set[str] = set()
        self._worklist: list[str] = []
        self._resolved_typedefs: set[str] = set()
        # Every reach of a record, accumulated -- not just the *first*
        # (Codex review, fresh evidence, two rounds). Reaching a record is
        # still deduplicated for *queuing* purposes (a record's fields are
        # only ever walked once, matching ``reach_record``'s own
        # pre-existing "queue once" semantics for the worklist), but the
        # previous version stored only the *first* reach's provenance --
        # meaning a record reached first via an ambiguous typedef alias and
        # only *later* also via a trustworthy direct declaration kept the
        # ambiguous provenance forever, making the confirmation result
        # depend on declaration order (confirmed empirically: reversing two
        # otherwise-unrelated declarations changed a genuine layout break
        # from confirmed to `UNKNOWN_UNRESOLVED`) -- exactly the class of
        # order-dependence this whole module has repeatedly had to close
        # elsewhere (see :func:`_seed_scan_from_public_declarations`'s own
        # ``stop_when_exhausted`` docstring for the analogous fix on stdlib
        # identities). Safe to accumulate rather than gate on "first only":
        # :func:`_seed_scan_from_public_declarations` runs to completion
        # (``full_scan=True`` disables its own early exit) *before*
        # :func:`_walk_reached_records` ever starts popping the worklist, so
        # every seed-level reach of a record is already accumulated here by
        # the time that record's own fields are scanned -- see
        # ``_record_direct``/``_record_typedef_origins`` below.
        self._record_direct: set[str] = set()
        self._record_typedef_origins: dict[str, set[str]] = {}
        # Which reached records have already had their own fields/bases
        # scanned at least once -- lets a later provenance upgrade
        # discovered *during the record walk itself* (not just during
        # seeding) requeue an already-walked record for a rescan (Codex
        # review, fresh evidence: see :meth:`reach_record`'s own docstring).
        self._record_walked: set[str] = set()
        # Per-alias cache of exact/any-spelling stdlib identities reachable
        # from that alias's own target chain, memoized once per alias
        # (Codex review, fresh evidence): the previous version re-walked an
        # already-resolved alias's *entire remaining tail* from scratch on
        # every subsequent declaration that named it, making a snapshot
        # with N chained aliases named by N separate declarations
        # (deepest-first) quadratic overall -- confirmed empirically (a
        # 1,200-alias chain took several seconds). See
        # :meth:`_reachable_stdlib`.
        self._alias_reachable: dict[
            str, tuple[frozenset[str], frozenset[str], frozenset[str]]
        ] = {}

    @property
    def exhausted(self) -> bool:
        """True once every stdlib candidate has been accounted for.

        Both seeding loops stop early on this: there is nothing left to find.
        """
        return not self._remaining

    def scan(
        self,
        type_string: str,
        *,
        via_typedef: bool = False,
        origin_alias: str | None = None,
    ) -> None:
        """Record every stdlib/non-stdlib identity *type_string* names;
        newly-reached non-stdlib records are queued for their own fields to
        be walked in turn. Also follows a typedef alias to its own target
        (Codex review, fresh evidence: ``surface.py``'s own reachability
        closure does the same), so a public signature spelled with a
        user-defined alias name still reaches the record it actually
        names.

        *via_typedef* and *origin_alias* (both default to "not scanning a
        typedef target" -- never set by an external caller, only by this
        method's own typedef branch below) together answer "is this call
        scanning the declaration's own literal text, or a typedef *target*
        string reached by resolving an alias, and if the latter, which
        top-level alias spelling did the real declaration actually write?"
        A genuinely *unambiguous* typedef alias -- one
        :func:`_typedef_spelling_targets` already resolved to exactly one
        target -- is real proof the declaration named that identity, even
        when the *target's* own bare form happens to collide with an
        unrelated stdlib sibling elsewhere in the snapshot; only an
        *alias* that is itself ambiguous, e.g. with an unrelated enum's
        bare spelling, must not confer exactness. A nested typedef
        resolution (a target string that itself contains another alias)
        propagates the *original* top-level alias unchanged, since that is
        the only spelling the real declaration ever actually wrote.

        The typedef branch below only ever fully re-walks a given alias's
        target *once*, guarded by ``_resolved_typedefs`` (records reached
        and ``_referenced`` populated) -- but a *second*, independent
        declaration reaching the same already-walked target through a
        *different*, this-time-unambiguous alias still needs its own
        provenance recorded: see :meth:`_reachable_stdlib`, the memoized
        lookup that records just that provenance without repeating the
        full walk.

        Alias-chain expansion (both the fresh-resolution branch and the
        already-resolved provenance-propagation branch) is driven by an
        explicit worklist, not Python call-stack recursion (Codex review,
        fresh evidence: a snapshot with ~1,000 chained typedef aliases,
        exposed deepest-first by its own public declarations, reproduced a
        real ``RecursionError`` on both the fresh-resolution path and
        :meth:`_propagate_typedef_provenance`'s own recursive call --
        confirmed empirically, and this method's own recursive
        ``self.scan(target, via_typedef=True, ...)`` call had the identical
        exposure even for a *single* declaration naming the outermost alias
        of a long chain). Mirrors :func:`_finditer_allow_nested`'s own
        stack-based rewrite for the same class of unbounded-nesting risk.
        """
        if not type_string:
            return
        self._scan_stdlib_and_records(type_string, via_typedef, origin_alias)
        if self._typedef_pattern is None:
            return
        # Each entry: (alias spelling, was this alias reached via a typedef
        # target rather than the caller's own literal text, the top-level
        # alias spelling to credit for it). Order is irrelevant for
        # correctness -- every effect below is set-membership-based, so
        # popping in any order reaches the same final state -- only for
        # avoiding recursion.
        worklist: list[tuple[str, bool, str | None]] = [
            (m.group(0), via_typedef, origin_alias)
            for m in _finditer_allow_nested(self._typedef_pattern, type_string)
        ]
        while worklist:
            alias, via_td, origin = worklist.pop()
            target = self._typedef_targets[alias]
            this_origin = origin if via_td else alias
            if alias not in self._resolved_typedefs:
                self._resolved_typedefs.add(alias)
                self._scan_stdlib_and_records(target, True, this_origin)
                if self._typedef_pattern is not None:
                    worklist.extend(
                        (m.group(0), True, this_origin)
                        for m in _finditer_allow_nested(self._typedef_pattern, target)
                    )
            else:
                # Already fully walked (records reached, `_referenced`
                # populated) via a *different* origin alias -- but this
                # alias's own exact-match provenance was never recorded:
                # `_resolved_typedefs` exists to stop the expensive
                # record-walk/`_referenced` bookkeeping from repeating,
                # not to gate provenance -- an earlier, ambiguous alias
                # resolving through the same target first would
                # otherwise silently swallow a later, genuinely
                # unambiguous alias's own provenance purely because of
                # declaration order. Cheaply re-derive just this
                # alias's own exact-match provenance without repeating
                # the full walk. `this_origin` is always a real alias
                # spelling here in practice (only ``None`` when
                # ``via_typedef`` is externally forced ``True`` with no
                # ``origin_alias``, which no caller in this module ever
                # does), but the type checker cannot see that.
                if this_origin is not None:
                    exact_ids, any_ids, records = self._reachable_stdlib(alias)
                    for identity in any_ids:
                        self._trusted_via_alias.setdefault(identity, set()).add(
                            this_origin
                        )
                    for identity in exact_ids:
                        self._exact_typedef_aliases.setdefault(identity, set()).add(
                            this_origin
                        )
                    # A typedef chain can terminate at a record rather than
                    # directly at a stdlib identity (Codex review, fresh
                    # evidence) -- credit this alias as an additional origin
                    # for reaching it, the same way a fresh resolution
                    # already does via `_scan_stdlib_and_records`'s own
                    # record_pattern loop.
                    for record_identity in records:
                        self.reach_record(
                            record_identity, via_typedef=True, origin_alias=this_origin
                        )

    def _scan_stdlib_and_records(
        self, type_string: str, via_typedef: bool, origin_alias: str | None
    ) -> None:
        """The stdlib-identity and non-stdlib-record halves of :meth:`scan`,
        factored out so both the top-level call and each typedef-chain
        worklist entry can reuse them without recursing back into
        :meth:`scan` itself (see its own docstring for why). *via_typedef*/
        *origin_alias* carry the exact same meaning as on :meth:`scan`.
        """
        for match in _finditer_allow_nested(self._stdlib_pattern, type_string):
            spelling = match.group(0)
            for identity in self._stdlib_index.get(spelling, ()):
                if identity in self._remaining:
                    self._referenced.add(identity)
                    self._remaining.discard(identity)
                # Broader "trustworthy at all" tracking, independent of
                # whether *spelling* is the identity's own self-key or a
                # derived/stripped form -- see ``_referenced_trusted``'s own
                # docstring on ``__init__`` for why this is needed alongside
                # the narrower exactness tracking below.
                if not via_typedef:
                    self._referenced_trusted.add(identity)
                elif origin_alias is not None:
                    self._trusted_via_alias.setdefault(identity, set()).add(
                        origin_alias
                    )
                if spelling != identity:
                    continue
                # The matched key is the identity's own self-key, never a
                # derived/stripped spelling (a stripped form is always
                # strictly shorter than the identity it derives from, since
                # it drops a non-empty namespace prefix) -- so this
                # occurrence alone proves the identity unambiguously,
                # independent of whatever else that identity's own derived
                # spelling might collide with -- **provided the text that
                # produced it is trustworthy**: a direct match in the
                # declaration's own literal text (``via_typedef=False``)
                # always is. A match found only while recursively scanning
                # a typedef's *target* string is trustworthy only when the
                # *alias* that led there is itself unambiguous -- recorded
                # here, not decided here, since only the caller (which
                # knows the full, enum-aware non-stdlib collision
                # vocabulary) can judge that; see
                # :meth:`referenced_exact_typedef_aliases`.
                if not via_typedef:
                    self._referenced_exact.add(identity)
                elif origin_alias is not None:
                    self._exact_typedef_aliases.setdefault(identity, set()).add(
                        origin_alias
                    )
        if self._record_pattern is not None:
            for match in _finditer_allow_nested(self._record_pattern, type_string):
                for identity in self._record_index.get(match.group(0), ()):
                    self.reach_record(
                        identity, via_typedef=via_typedef, origin_alias=origin_alias
                    )

    def _reachable_stdlib(
        self, start: str
    ) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
        """``(exact, any, records)`` -- the stdlib identities reachable from
        *start* alias's own target (exact self-key matches and any-spelling
        matches respectively), plus every non-stdlib record identity
        directly named anywhere in that same chain, transitively through
        further nested typedef aliases.

        ``records`` (Codex review, fresh evidence, closing a gap the
        previous revision left open) matters because a typedef chain can
        terminate at a *record*, not directly at a stdlib identity --
        ``ns::bad -> Good -> Wrapper`` (``Wrapper`` a record with its own
        stdlib field), with ``bad`` colliding with an unrelated enum's bare
        spelling. A first declaration spelling ``bad`` resolves the whole
        chain fresh, reaching ``Wrapper`` with ``bad`` as its only
        (ambiguous) origin. A *second* declaration spelling the
        already-resolved, genuinely unambiguous ``Good`` directly used to
        credit nothing at all for ``Wrapper``, since the cache tracked only
        stdlib identities -- silently leaving a real layout break
        unconfirmed even though ``Good`` is real, independent proof.
        :meth:`scan`'s own "already resolved" branch now also calls
        :meth:`reach_record` for every identity in ``records`` here, the
        same way it already credits ``exact``/``any`` for stdlib
        identities. Never expands *into* a reached record's own fields --
        that stays the separate, independently-threaded mechanism
        :meth:`reach_record`/:func:`_walk_reached_records` already own; a
        record found here is a *terminal* node for this structural walk,
        matching how the fresh-resolution path (:meth:`scan`'s own typedef
        branch calling :meth:`_scan_stdlib_and_records`) already stops
        similarly -- it queues a reached record for :func:`_walk_reached_records`
        to walk later, rather than walking it inline.

        Memoized per alias in ``_alias_reachable`` via an explicit,
        iterative post-order stack walk -- not recursion, and not a plain
        re-scan of *start*'s raw text on every call (Codex review, fresh
        evidence: see ``_alias_reachable``'s own docstring on ``__init__``
        for the quadratic blowup this closes). Each alias's own reachable
        set is computed at most once for the lifetime of this scan,
        regardless of how many different declarations later name it once
        already resolved.

        A cyclic typedef chain is broken conservatively: an alias currently
        being computed (grey) contributes nothing to itself if reached
        again before it's done, rather than looping forever.
        """
        if start in self._alias_reachable:
            return self._alias_reachable[start]
        grey: set[str] = set()
        stack: list[tuple[str, bool]] = [(start, False)]
        while stack:
            alias, expanded = stack.pop()
            if not expanded:
                if alias in self._alias_reachable or alias in grey:
                    continue
                grey.add(alias)
                stack.append((alias, True))
                target = self._typedef_targets[alias]
                if self._typedef_pattern is not None:
                    for m in _finditer_allow_nested(self._typedef_pattern, target):
                        stack.append((m.group(0), False))
                continue
            target = self._typedef_targets[alias]
            exact: set[str] = set()
            any_ids: set[str] = set()
            records: set[str] = set()
            for m in _finditer_allow_nested(self._stdlib_pattern, target):
                spelling = m.group(0)
                for identity in self._stdlib_index.get(spelling, ()):
                    any_ids.add(identity)
                    if spelling == identity:
                        exact.add(identity)
            if self._record_pattern is not None:
                for m in _finditer_allow_nested(self._record_pattern, target):
                    records.update(self._record_index.get(m.group(0), ()))
            if self._typedef_pattern is not None:
                for m in _finditer_allow_nested(self._typedef_pattern, target):
                    child_exact, child_any, child_records = self._alias_reachable.get(
                        m.group(0), (frozenset(), frozenset(), frozenset())
                    )
                    exact |= child_exact
                    any_ids |= child_any
                    records |= child_records
            self._alias_reachable[alias] = (
                frozenset(exact),
                frozenset(any_ids),
                frozenset(records),
            )
            grey.discard(alias)
        return self._alias_reachable[start]

    def reach_record(
        self,
        identity: str,
        *,
        via_typedef: bool = False,
        origin_alias: str | None = None,
    ) -> None:
        """Queue a non-stdlib record's own fields/bases to be walked, and
        accumulate *via_typedef*/*origin_alias* provenance for *every*
        reach, not only the first (Codex review, fresh evidence, three
        rounds -- see ``_record_direct``/``_record_typedef_origins``'s own
        docstring on ``__init__`` for why accumulating instead of "first
        reach wins" is required for order-independence).
        :func:`_walk_reached_records` reads this accumulated state via
        :meth:`record_provenance` so a record's own fields are scanned with
        every trust level it was ever reached under, instead of
        unconditionally trusting them regardless of how ambiguously the
        record itself was reached.

        A record already walked once (its fields already scanned, tracked
        in ``_record_walked`` via :meth:`mark_record_walked`) is *requeued*
        when this reach genuinely upgrades its provenance -- adds it to
        ``_record_direct`` for the first time, or adds a typedef origin it
        didn't already have (Codex review, fresh evidence: a record reached
        first through an ambiguous typedef alias during seeding, and only
        *later* also directly through another already-reached record's own
        field discovered *during the record walk itself* -- not during
        seeding, so accumulation-before-the-walk-starts doesn't cover it --
        stayed stuck with its stale, ambiguous-only provenance forever,
        since the worklist's own "queue once" dedup meant it was never
        walked again to pick up the upgrade). Requeuing is safe to repeat:
        each record can only be upgraded a bounded number of times (once
        for the direct flag, once per distinct alias in the snapshot), so
        this always terminates, and re-scanning the same field text again
        is idempotent -- it only ever adds more identities/credits, never
        removes any.
        """
        upgraded = False
        if not via_typedef:
            if identity not in self._record_direct:
                self._record_direct.add(identity)
                upgraded = True
        elif origin_alias is not None:
            origins = self._record_typedef_origins.setdefault(identity, set())
            if origin_alias not in origins:
                origins.add(origin_alias)
                upgraded = True
        if identity not in self._reached_records:
            self._reached_records.add(identity)
            self._worklist.append(identity)
        elif upgraded and identity in self._record_walked:
            self._worklist.append(identity)

    def mark_record_walked(self, identity: str) -> None:
        """Record that *identity*'s own fields/bases were just scanned,
        so a later provenance upgrade (see :meth:`reach_record`) knows to
        requeue it for a rescan instead of assuming it's still pending."""
        self._record_walked.add(identity)

    def record_provenance(self, identity: str) -> tuple[bool, frozenset[str]]:
        """``(is_direct, typedef_origins)`` -- every trust level *identity*
        (a reached record) has ever been reached under. ``is_direct`` is
        ``True`` as soon as *any* reach was ``via_typedef=False``
        (unconditionally trustworthy on its own, matching this whole
        module's "one genuinely unambiguous route is real proof" principle
        used everywhere else); ``typedef_origins`` is every top-level alias
        spelling that reached it only via a typedef, to be judged the same
        way :meth:`referenced_exact_typedef_aliases` already is by the
        outer caller."""
        return (
            identity in self._record_direct,
            frozenset(self._record_typedef_origins.get(identity, ())),
        )

    def next_reached_record(self) -> str | None:
        """Pop the next queued record identity, or ``None`` when the queue
        is empty."""
        return self._worklist.pop() if self._worklist else None

    def referenced(self) -> frozenset[str]:
        """The stdlib identities this walk proved directly referenced."""
        return frozenset(self._referenced)

    def referenced_exact(self) -> frozenset[str]:
        """Which of :meth:`referenced`'s identities were matched at least
        once via their own literal, un-derived spelling **in a real
        declaration's own text** -- i.e. proven unambiguously, independent
        of any collision a *derived* (stripped/bare) spelling might have
        with a sibling stdlib identity or an unrelated non-stdlib
        record/enum. A subset of :meth:`referenced`. Does **not** include
        an identity matched only while recursively scanning a typedef's
        target string -- see :meth:`referenced_exact_typedef_aliases` for
        that route's own, alias-conditional provenance."""
        return frozenset(self._referenced_exact)

    def referenced_exact_typedef_aliases(self) -> dict[str, frozenset[str]]:
        """For each identity matched exactly only while recursively
        scanning a typedef's *target* string (never in a real declaration's
        own literal text): every top-level alias spelling that led there.

        The scan itself cannot decide whether such a match is trustworthy --
        that depends on whether the alias collides with an unrelated
        non-stdlib record/enum spelling, and only the caller (specifically
        :func:`directly_referenced_stdlib_type_spellings`, which already
        computes that enum-aware collision vocabulary for its own separate
        guard) has that information. An identity is safe to treat as exact
        here as soon as *any* alias that produced it is absent from the
        caller's own non-stdlib collision set -- one genuinely unambiguous
        route is real proof regardless of how many other, separately
        ambiguous aliases also happened to reach the same identity.
        """
        return {k: frozenset(v) for k, v in self._exact_typedef_aliases.items()}

    def referenced_trusted(self) -> frozenset[str]:
        """Which of :meth:`referenced`'s identities were matched at least
        once via *any* spelling (self-key or a derived/stripped form alike)
        in a route this scan considers unconditionally trustworthy (a real
        declaration's own literal text, or a reached record's own field/
        base scanned with that same trust level -- see
        :meth:`reach_record`). Broader than :meth:`referenced_exact` (which
        additionally requires the self-key spelling specifically); see
        ``_referenced_trusted``'s own docstring on ``__init__`` for why the
        broader form is needed."""
        return frozenset(self._referenced_trusted)

    def trusted_via_alias(self) -> dict[str, frozenset[str]]:
        """The alias-conditional sibling of :meth:`referenced_trusted`,
        mirroring :meth:`referenced_exact_typedef_aliases` but for *any*
        spelling rather than only the self-key one -- see that method's own
        docstring for how a caller is expected to use this (trust an
        identity here as soon as any one alias that produced it is itself
        unambiguous)."""
        return {k: frozenset(v) for k, v in self._trusted_via_alias.items()}


def _seed_scan_from_public_declarations(
    snapshot: AbiSnapshot,
    scan: _StdlibReferenceScan,
    non_stdlib_identities: frozenset[str],
    *,
    exclude_export_only: bool = False,
    committed_roots: frozenset[str] | None = None,
    stop_when_exhausted: bool = True,
) -> None:
    """Scan every public, non-stdlib function signature and variable type.

    A member function additionally seeds its *owner* class — a public method
    never repeats its own class in its return/parameter types, so without this
    the owner's fields would never be walked. The owner is queued only on an
    *exact* identity match, never through ``record_index``'s suffix matching:
    ``owner_class_of`` cannot tell an enclosing class from an enclosing
    namespace, so a bare namespace fragment could otherwise collide with an
    unrelated internal record's bare suffix (see the public function's
    docstring).

    ``exclude_export_only``/``committed_roots`` are forwarded to
    :func:`_is_public_non_stdlib_declaration` unchanged — see its own
    docstring.

    ``stop_when_exhausted`` (default ``True``, :func:`directly_referenced_stdlib_types`'s
    own performance-motivated early exit -- "found via any route" is all that
    function needs, so scanning further declarations once every candidate is
    accounted for is pure waste) must be ``False`` for a caller that also
    needs per-identity *exact-match* provenance: an identity first found via
    an ambiguous derived spelling in one declaration, with its own
    unambiguous exact spelling appearing only in a *later* declaration,
    would never have that later occurrence scanned once ``_remaining`` is
    already empty -- silently making
    :func:`directly_referenced_stdlib_type_spellings`'s result depend on
    declaration order (confirmed empirically by reversing two function
    declarations). Set ``False`` there; the ordinary
    :func:`directly_referenced_stdlib_types` path is unaffected.
    """
    for fn in snapshot.functions:
        if stop_when_exhausted and scan.exhausted:
            break
        if not _is_public_non_stdlib_declaration(
            fn,
            exclude_export_only=exclude_export_only,
            committed_roots=committed_roots,
        ):
            continue
        scan.scan(fn.return_type)
        for param in fn.params:
            scan.scan(param.type)
        owner = owner_class_of(fn)
        if owner is not None and owner in non_stdlib_identities:
            scan.reach_record(owner)

    for var in snapshot.variables:
        if stop_when_exhausted and scan.exhausted:
            break
        if not _is_public_non_stdlib_declaration(
            var,
            exclude_export_only=exclude_export_only,
            committed_roots=committed_roots,
        ):
            continue
        scan.scan(var.type)


def _walk_reached_records(
    scan: _StdlibReferenceScan,
    non_stdlib_records: dict[str, list[RecordType]],
    *,
    exclude_export_only: bool = False,
    stop_when_exhausted: bool = True,
) -> None:
    """Walk each reached non-stdlib record's own fields and bases, transitively.

    Every entry sharing the reached identity is walked, each checking its own
    ``origin`` independently: a private-origin duplicate excludes only itself,
    not a public-origin sibling of the same identity.

    ``exclude_export_only``, same meaning as
    :func:`_is_public_non_stdlib_declaration`'s own parameter: a record
    defined only via the binary's export table (no header at all) must not
    contribute its fields as public-header-domain evidence either, for the
    same reason a bare export-only function/variable root must not.

    ``stop_when_exhausted`` mirrors
    :func:`_seed_scan_from_public_declarations`'s own parameter -- see its
    docstring.

    Each record's own fields/bases are scanned with *every* trust level the
    record itself has ever been reached under, accumulated across every
    declaration seeding it and every other record's own field/base that
    also reaches it during this same walk (Codex review, fresh evidence,
    three rounds: see ``_StdlibReferenceScan.reach_record``'s own
    docstring) -- a record reached only through an ambiguous typedef alias
    must not have a stdlib type named directly in one of its own fields
    treated as unconditionally exact/trusted just because the field's own
    text is "direct", but a record reached via *any* trustworthy route
    (direct, or an alias later found unambiguous) must not have that
    route's confirmation depend on which declaration -- or which other
    record's own field, discovered only *during* this walk -- happened to
    be processed first. ``scan.mark_record_walked`` after scanning a
    record's own fields/bases lets a later provenance upgrade (a route
    discovered only once this record's own popped-and-scanned already)
    requeue it for a rescan instead of silently keeping the stale state.
    """
    while True:
        if stop_when_exhausted and scan.exhausted:
            return
        identity = scan.next_reached_record()
        if identity is None:
            return
        is_direct, typedef_origins = scan.record_provenance(identity)
        for rec in non_stdlib_records[identity]:
            if rec.origin in _NON_PUBLIC_ORIGINS:
                continue
            if exclude_export_only and rec.origin is ScopeOrigin.EXPORT_ONLY:
                continue
            texts = [f.type for f in rec.fields] + [
                *rec.bases,
                *rec.virtual_bases,
            ]
            # Both direct and virtual bases are ABI-reachable through the
            # derived type (virtual inheritance still embeds the base
            # subobject + vtable path), same as surface.py's own closure
            # (Codex review, fresh evidence): a public Derived inheriting a
            # non-stdlib Base whose own field is a stdlib record was
            # otherwise never reached, since only rec.fields was followed.
            for text in texts:
                if is_direct:
                    scan.scan(text)
                # Scanned once per distinct alias that reached this record
                # (not just one) -- one genuinely unambiguous route among
                # several is real proof, the same principle already applied
                # to a plain stdlib identity's own multi-alias provenance.
                for alias in typedef_origins:
                    scan.scan(text, via_typedef=True, origin_alias=alias)
        scan.mark_record_walked(identity)


def _run_stdlib_reference_scan(
    snapshot: AbiSnapshot,
    *,
    exclude_export_only: bool = False,
    committed_roots: frozenset[str] | None = None,
    full_scan: bool = False,
) -> _StdlibReferenceScan | None:
    """Run the walk :func:`directly_referenced_stdlib_types` performs and
    return the completed :class:`_StdlibReferenceScan` itself, or ``None``
    when *snapshot* carries no stdlib-namespaced types at all (mirroring
    that function's own early return).

    Factored out so a caller needing more than the flat ``referenced()``
    set -- specifically :func:`directly_referenced_stdlib_type_spellings`,
    which also needs :meth:`_StdlibReferenceScan.referenced_exact` and
    :meth:`_StdlibReferenceScan.referenced_exact_typedef_aliases` -- can run
    the identical walk once rather than either re-deriving it or being
    limited to :func:`directly_referenced_stdlib_types`'s own routeless
    return value.

    ``exclude_export_only``/``committed_roots`` are passed straight through
    to :func:`_seed_scan_from_public_declarations`/
    :func:`_walk_reached_records`/:func:`_is_public_non_stdlib_declaration`
    -- see their own docstrings. ``committed_roots`` is not threaded into
    :func:`_walk_reached_records` -- that function walks a non-stdlib
    record's own fields/bases once the record is already reached from a
    committed seed, so the record's own commitment status is moot; only the
    seed declarations that can *initiate* reachability need the check.

    *full_scan* (default ``False``, matching :func:`directly_referenced_stdlib_types`'s
    own early-exit behavior) disables the "stop once every candidate is
    accounted for" optimization -- pass ``True`` for exact-match provenance
    to be complete regardless of declaration order (see
    :func:`_seed_scan_from_public_declarations`'s own ``stop_when_exhausted``
    docstring for the failure mode this closes).
    """
    stdlib_identities, non_stdlib_identities, non_stdlib_records = (
        _partition_snapshot_types(snapshot)
    )
    if not stdlib_identities:
        return None

    # Threaded into the scan's own record_index construction (Codex review,
    # fresh evidence) so a record reached only via a bare spelling that
    # collides with an unrelated enum is never treated as an unconditionally
    # trustworthy direct match -- see _spelling_index's own docstring.
    enum_identities = frozenset(
        _record_identity(en.name, en.qualified_name) for en in snapshot.enums
    )
    scan = _StdlibReferenceScan(
        stdlib_identities,
        non_stdlib_identities,
        dict(snapshot.typedefs),
        enum_identities,
    )
    stop_when_exhausted = not full_scan
    _seed_scan_from_public_declarations(
        snapshot,
        scan,
        non_stdlib_identities,
        exclude_export_only=exclude_export_only,
        committed_roots=committed_roots,
        stop_when_exhausted=stop_when_exhausted,
    )
    _walk_reached_records(
        scan,
        non_stdlib_records,
        exclude_export_only=exclude_export_only,
        stop_when_exhausted=stop_when_exhausted,
    )
    return scan


def directly_referenced_stdlib_types(
    snapshot: AbiSnapshot, *, exclude_export_only_roots: bool = False
) -> frozenset[str]:
    """Stdlib/runtime-namespaced :class:`RecordType` names in *snapshot* that
    are directly referenced by a **public**, non-stdlib function's
    return/parameter type or a non-stdlib :class:`RecordType`'s own field
    type.

    Returns the empty set when the snapshot carries no stdlib-namespaced
    types at all (the common case) — never an error. Deliberately a single,
    snapshot-scoped, pure computation: no build/source evidence, no template
    argument resolution beyond substring matching, so a stdlib type
    mentioned only inside another stdlib type's own template arguments
    (never surfacing in a non-stdlib declaration) is correctly excluded.

    Candidate identification uses ``qualified_name or name`` (Codex review,
    fresh evidence), not ``name`` alone: castxml/direct-clang record the bare
    leaf in ``name`` and the namespace-qualified spelling separately in
    ``qualified_name``, so ``name`` alone never carries a ``std::`` prefix
    for those two backends and this helper would silently find nothing. See
    :func:`_stripped_signature_spelling`/:func:`_spelling_index` for how the
    resulting identity is matched back against the (differently-spelled,
    possibly ambiguous) signature type strings, and
    :func:`_compile_spelling_pattern` for why the matching itself is one
    compiled regex rather than a per-candidate substring scan.

    A ``Function`` whose ``visibility`` is not :attr:`Visibility.PUBLIC`
    (``HIDDEN``/``ELF_ONLY``) is never itself the referencing side (Codex
    review): a real snapshot can retain such a function for cross-reference
    purposes even though it is not part of the public ABI surface this
    helper is meant to model, and treating its signature as equivalent to a
    public one would turn an internal implementation detail into a
    stdlib-ABI dependency that isn't real. Same reasoning applies to
    ``origin`` (Codex review, fresh evidence): public-header scoping can
    retain a function whose ``visibility`` is still ``PUBLIC`` but whose
    ``origin`` is ``ScopeOrigin.PRIVATE_HEADER``/``SYSTEM_HEADER``/
    ``GENERATED`` — linkage and origin are independent axes (ADR-024 D1),
    so a function only ever declared in a private/system/generated header
    is rejected here too, before its signature is ever scanned. A public
    ``Variable`` is seeded the same way (Codex review, fresh evidence: a
    ``compute_public_surface()``-style closure already treats public
    variables as type roots, but this scan originally only walked
    ``snapshot.functions`` — a public exported global like ``Foo global``
    never seeded ``Foo`` at all).

    Both loops also check the declaration's own *recovered qualified name*
    (:func:`abicheck.diff_cxx_rules.itanium_qualified_name`, from
    ``mangled``) against ``STDLIB_TYPE_NAMESPACE_PREFIXES``, not just the
    bare ``name``/``fn.name`` field (Codex review, fresh evidence):
    CastXML/direct-clang record a function or namespace-scope variable's
    own display name bare (e.g. ``"touch"``, never
    ``"__gnu_cxx::Node::touch"`` or ``"std::touch"``), so the plain
    ``name.startswith(...)`` check cannot catch a retained, seemingly-
    public declaration that is actually part of the standard library
    itself — a stdlib-internal method or a namespace-scope stdlib variable
    — whose return type/params/type mentioning a stdlib record would
    otherwise be scanned and incorrectly marked directly referenced,
    unfiltering purely internal toolchain churn as a public break. This
    check subsumes (and replaces) an earlier, narrower version that only
    checked the *owner* class recovered by ``owner_class_of`` before
    seeding it: whenever that owner starts with a stdlib prefix, the full
    recovered qualified name (owner plus its own trailing member) always
    does too, so the owner-only check could never fire without this
    broader one already having skipped the declaration entirely — and the
    broader check additionally catches a stdlib namespace's own direct
    free function/variable (a single mangled scope component, e.g.
    ``"std::touch"``), which the owner-only check missed since
    ``owner_class_of`` returns a bare ``"std"`` (no trailing ``"::"``) for
    that shape, never matching the ``"std::"`` prefix string.

    A signature/field type string spelled with a user-defined typedef alias
    (e.g. a public function returning ``Alias`` where ``snapshot.typedefs``
    maps ``"Alias"`` to ``"Foo"``) is resolved to its target and scanned in
    turn (Codex review, fresh evidence: ``surface.py``'s own reachability
    closure does the same) — this is a different, already-solvable case
    from the typedef-*aliased stdlib type* gap noted below (``std::string``
    naming its own alias with no reverse mapping back to the owning
    ``RecordType``): here the alias's target is a plain type-string
    substitution already present in ``snapshot.typedefs``, nothing needs
    inventing.

    A non-stdlib record's own fields *and bases* (both direct and virtual —
    Codex review, fresh evidence: a public ``Derived`` inheriting a
    non-stdlib ``Base`` whose own field is a stdlib record was otherwise
    never reached, since only ``rec.fields`` was followed; mirrors
    ``surface.py``'s own closure, which follows both for the same reason —
    virtual inheritance still embeds the base subobject + vtable path) are
    only consulted once that record itself is confirmed reachable from a
    public root — by direct mention in a public function's own signature,
    by being that function's *owner* class/struct for a member function
    (Codex review, fresh evidence: a public method like ``void Foo::run()``
    never repeats ``Foo`` in its own return/parameter types, so without
    also seeding :func:`abicheck.diff_cxx_rules.owner_class_of` the
    previous version never queued ``Foo`` at all — a genuine layout break
    in one of its fields would be silently missed. A retained, seemingly-
    public method whose *owner* is itself a stdlib-internal class, e.g.
    libstdc++'s ``__gnu_cxx::Node``, is excluded from this by the
    declaration-level stdlib-scope check above, before its owner is ever
    computed), or transitively
    through another already-reachable record's fields/bases (Codex review,
    fresh evidence: the previous version scanned *every* non-stdlib
    record's fields unconditionally, so a purely internal,
    never-actually-reachable record — e.g. one a DWARF-only snapshot
    retains with the default
    ``ScopeOrigin.UNKNOWN`` even though nothing public touches it — could
    still make an unrelated stdlib type look directly referenced). A
    record's own ``origin`` being ``PRIVATE_HEADER``/``SYSTEM_HEADER``/
    ``GENERATED`` still excludes its fields from the walk, same as before.
    See :func:`_spelling_index` for why an ambiguous bare alias shared by
    two distinct non-stdlib records (Codex review, fresh evidence) is
    dropped rather than queuing both.

    An owner recovered from :func:`abicheck.diff_cxx_rules.owner_class_of`
    is queued only on an *exact* match against a non-stdlib record's full
    identity — never through the general suffix-matching mechanism
    :func:`_spelling_index`'s ``record_index`` uses for signature type
    spellings (Codex review, fresh evidence): ``owner_class_of`` derives
    its result by chopping the trailing ``"::"``-component off *any*
    already-qualified declaration name or mangled-symbol scope chain, with
    no way to tell whether what remains is really an enclosing *class* or
    just an enclosing *namespace* — e.g. a public namespace function
    ``api::run()`` makes ``owner_class_of`` return the bare namespace
    fragment ``"api"``, which could coincidentally equal the *bare suffix*
    of some unrelated internal record ``other::api``, wrongly walking that
    record's fields. Unlike a real signature type spelling (which a
    backend can legitimately partially-qualify per the ``Outer::Inner``
    case below), an owner string is always either the full, exact
    identity of a genuine class (both ``owner_class_of``'s
    already-qualified-name path and its mangled-decomposition fallback
    reconstruct the *complete* scope chain, never a partially-elided one)
    or, when the function is not actually a method, semantic noise —
    so exact-identity matching is both sufficient for every real class
    owner and immune to the namespace-collision false positive.
    Deliberately does **not** also gate on pointer-vs-by-value use the way
    a first read might expect (an earlier review round raised this): this
    module intentionally mirrors ``surface.py``'s own documented ADR-024 §D3
    position — a pointer-reached, non-opaque stdlib type is still
    layout-observable elsewhere (a consumer can dereference or allocate it
    by value), so demoting it here would risk hiding a real break. The safe
    half of that precision (a pointer-only-reached *opaque* handle) is
    already handled downstream by the existing opaque-size-change filter
    (``diff_filtering._filter_opaque_size_changes``, gated on
    ``RecordType.is_opaque``), not by this reachability computation.

    ``exclude_export_only_roots``, when set, additionally excludes any root
    or reached record whose ``origin`` is ``ScopeOrigin.EXPORT_ONLY`` — see
    :func:`_is_public_non_stdlib_declaration`'s own docstring for why a
    caller building public-header-domain contract evidence
    (``directly_referenced_stdlib_type_spellings``, used by
    ``contract_pipeline.py``) must set this, while this function's other,
    evidence-tier-agnostic caller (``diff_types.py``) leaves it at the
    default ``False``.
    """
    scan = _run_stdlib_reference_scan(
        snapshot, exclude_export_only=exclude_export_only_roots
    )
    return scan.referenced() if scan is not None else frozenset()


def directly_referenced_stdlib_type_spellings(
    snapshot: AbiSnapshot,
    *,
    exclude_export_only_roots: bool = False,
    committed_roots: frozenset[str] | None = None,
) -> frozenset[str]:
    """:func:`directly_referenced_stdlib_types`, re-expressed in the spelling
    a finding's own ``symbol``/``caused_by_type`` actually carries, for a
    caller that needs to match against those fields rather than against
    ``RecordType`` identities.

    ``directly_referenced_stdlib_types`` returns each type's *identity* --
    ``qualified_name or name`` (see :func:`_record_identity`), the
    fully-qualified spelling. A ``Change``'s own ``symbol``/``caused_by_type``
    is populated from ``diff_types.py``'s comparison of two ``RecordType``
    entries' own ``name`` fields, which per-backend may be that same
    identity (DWARF bakes the qualified form directly into ``name``) or the
    namespace-prefix-stripped form a signature actually spells it with
    (castxml/direct-clang keep ``name`` bare) -- see
    :func:`_stripped_signature_spelling`'s own docstring for the empirical
    basis. Returning the union of both forms for every identity, rather than
    picking one, means a caller does not have to know which backend
    produced the snapshot it's matching against.

    Contract evaluation's own use case (confirming a layout-change finding
    on a stdlib type a public signature names outright, independent of
    ``surface.py``'s header-origin-scoped ``public_types`` closure, which
    deliberately excludes stdlib types as non-ABI-surface toolchain
    internals) is why this exists as a public, separately-tested function
    rather than an inline transform at the call site: reusing
    :func:`_stripped_signature_spelling` here is what keeps the two stdlib
    spelling normalizations (the one this module's own signature-matching
    index already performs internally, and the one a caller outside this
    module needs) from silently drifting apart.

    A stripped spelling that collides with an unrelated non-stdlib record's
    own signature spelling is dropped, mirroring :func:`_spelling_index`'s
    identical guard (Codex review, fresh evidence): a snapshot can carry its
    own, unrelated ``api::vector<int>`` whose bare signature spelling is the
    same ``"vector<int>"`` a real ``std::vector<int>`` strips to, and a
    ``Change`` on that unrelated user type carries that identical bare
    ``RecordType.name`` as its own ``symbol`` -- so exporting the collided
    spelling here would let contract evaluation confirm a finding about the
    user type using evidence about the unrelated stdlib type. Reuses
    :func:`_non_stdlib_signature_spellings` rather than re-deriving the
    collision set, the same reasoning :func:`_spelling_index` documents for
    its own use of it. The unstripped, fully-qualified ``identity`` is never
    guarded this way (matching :func:`_spelling_index`'s own asymmetry): a
    qualified stdlib spelling colliding with an unrelated type would require
    that type to live in a namespace literally named a stdlib prefix
    (``std::``, ...), which is reserved and not something a real snapshot
    encodes as a legitimate user declaration.

    A stripped spelling shared by **two or more distinct referenced stdlib
    identities** means neither identity's own presence in
    :func:`directly_referenced_stdlib_types`'s return value is independently
    confirmed (Codex review, fresh evidence, two rounds): e.g. a signature
    naming bare ``vector<int>`` cannot distinguish ``std::vector<int>`` from
    ``__gnu_debug::vector<int>``, so that scan correctly marks *both*
    referenced (never missing a real reference is the safe direction for
    its purpose -- deciding whether to keep a layout finding at all) purely
    because each shares the one spelling the signature actually contains,
    not because either was independently matched. A first fix dropped only
    the shared *stripped* spelling itself, reasoning each identity's own
    full qualified spelling was still safe to export -- reproduced wrong:
    a finding whose own ``symbol``/``caused_by_type`` happens to be spelled
    as the *full* qualified form of either ambiguous candidate (e.g. a
    DWARF-derived snapshot, which bakes the qualified spelling directly
    into ``name``) was then confirmed via that unconditionally-exported
    full identity, even though neither identity's reachability was ever
    independently established -- only one of the two, at most, is real, and
    the evidence cannot say which. Closed by excluding **every** spelling
    (stripped and full alike) for every identity in an ambiguous group, not
    only the shared stripped one: this function's own answer for that
    identity is exactly as unproven as the shared spelling that produced
    it. Unlike the non-stdlib collision above, this ambiguity can only be
    resolved among identities this function itself already has in hand, so
    it is computed locally rather than reusing a module-level helper.

    **Per-match-route provenance closes what an earlier revision left as a
    known conservative gap:** grouping identities purely by whether their
    stripped spelling collides with another *referenced* identity's cannot
    distinguish "reached only via the ambiguous shared spelling" from "also
    independently reached via its own unique full spelling elsewhere in the
    same snapshot" -- a whole ambiguous group would otherwise be excluded
    even when one member is separately, unambiguously confirmable another
    way. Closed with real per-identity match provenance:
    :class:`_StdlibReferenceScan` tracks, alongside its flat ``referenced()``
    set, which identities were matched at least once via their own literal,
    un-derived spelling in a real declaration's own text
    (:meth:`_StdlibReferenceScan.referenced_exact`), plus -- separately --
    which were matched only while recursively resolving a typedef's target,
    keyed by which top-level alias led there
    (:meth:`_StdlibReferenceScan.referenced_exact_typedef_aliases`). An
    identity in ``referenced_exact()`` is always kept regardless of any
    collision its *derived* spelling has, since the occurrence that proved
    it is independent of that collision. An identity reached only through a
    typedef alias is trusted the same way as soon as *any* alias that
    produced it is itself free of a collision against this function's own
    enum-aware, typedef-aware non-stdlib vocabulary -- one genuinely
    unambiguous route is real proof regardless of how many other, separately
    ambiguous routes also happened to reach the same identity. Collision
    counting spans every stdlib identity the *snapshot* carries (via
    :func:`_partition_snapshot_types`), not only the referenced subset: an
    unreferenced sibling sharing a referenced identity's bare spelling still
    makes that spelling untrustworthy for an unrelated finding's own
    ``Change.symbol`` to match against. The non-stdlib collision vocabulary
    itself spans both ``snapshot.types`` and ``snapshot.enums`` (a
    non-stdlib record and enum can share a bare backend spelling just as
    easily as two records can), and separately spans every spelling
    ``snapshot.typedefs`` could produce -- exact key or derived suffix,
    resolved or ambiguous alike (:func:`_typedef_candidate_spellings`) --
    since an unrelated typedef's own key can equally collide with a stdlib
    identity's stripped bare form. A typedef whose *resolved* target
    genuinely names the very identity being evaluated (spelled fully
    qualified, or in some other stdlib-namespaced shape reducing to the same
    bare form) is not treated as a collision at all.

    ``exclude_export_only_roots`` is forwarded to
    :func:`_run_stdlib_reference_scan` unchanged (Codex review, fresh
    evidence): contract evaluation's own public-header-domain use must set
    this, since an export-only declaration is exactly the evidence the
    separate ``exports`` domain exists to evaluate, not ``public``'s.

    ``committed_roots`` (default ``None``) mirrors
    ``compare --post-manifest``'s own scoping and is forwarded straight
    through to :func:`_run_stdlib_reference_scan` -- see
    :func:`_is_public_non_stdlib_declaration`'s own docstring for the exact
    membership rule and the gap it closes.

    Exact-match provenance must not depend on declaration order (Codex
    review, fresh evidence: reversing the order of two otherwise-unrelated
    function declarations changed this function's result for the same
    identity between "confirmed" and empty before this fix) -- closed by
    running :func:`_run_stdlib_reference_scan` with ``full_scan=True`` here,
    never for :func:`directly_referenced_stdlib_types` itself, which keeps
    the early exit ("found via any route" is all it needs).
    """
    scan = _run_stdlib_reference_scan(
        snapshot,
        full_scan=True,
        exclude_export_only=exclude_export_only_roots,
        committed_roots=committed_roots,
    )
    if scan is None:
        return frozenset()
    identities = scan.referenced()
    if not identities:
        return frozenset()
    all_stdlib_identities, non_stdlib_record_identities, _ = _partition_snapshot_types(
        snapshot
    )
    non_stdlib_enum_identities = frozenset(
        _record_identity(en.name, en.qualified_name) for en in snapshot.enums
    )
    non_stdlib_identities = non_stdlib_record_identities | non_stdlib_enum_identities
    non_stdlib_spellings = _non_stdlib_signature_spellings(non_stdlib_identities)
    # A match found via a real declaration's own literal text is always
    # trustworthy. A match found only while recursively scanning a typedef's
    # *target* string is trustworthy too, but only when at least one alias
    # that produced it is itself absent from this function's own
    # enum-aware `non_stdlib_spellings` collision set -- the scan itself
    # cannot judge that, since only this function computes that vocabulary.
    exact_mut = set(scan.referenced_exact())
    for identity, aliases in scan.referenced_exact_typedef_aliases().items():
        if aliases - non_stdlib_spellings:
            exact_mut.add(identity)
    exact = frozenset(exact_mut)
    # Broader sibling of `exact` above, mirroring the same alias-ambiguity
    # check but for *any* spelling (self-key or derived) rather than only
    # the self-key one -- needed below for the "stripped form collides with
    # nothing else in the snapshot" shortcut, which is a *different*
    # confirmation route than exactness and must not trust a match whose
    # own reachability was never itself proven trustworthy (Codex review,
    # fresh evidence: a stdlib type named directly in a record's own field,
    # where the record itself was reached only through an ambiguous
    # typedef alias, was previously confirmed unconditionally through this
    # shortcut regardless of that ambiguity -- confirmed empirically).
    trusted_mut = set(scan.referenced_trusted())
    for identity, aliases in scan.trusted_via_alias().items():
        if aliases - non_stdlib_spellings:
            trusted_mut.add(identity)
    trusted = frozenset(trusted_mut)
    # Every spelling a typedef could be reached by -- not just its literal
    # dict key -- via the same suffix-expansion _StdlibReferenceScan itself
    # already uses to resolve a typedef alias to its target: a raw
    # `snapshot.typedefs.get(stripped)` lookup misses a namespace-qualified
    # key like `"mine::vector<int>"` whose *derived* suffix `"vector<int>"`
    # is what actually collides with a stdlib identity's own stripped
    # spelling, since the dict key itself is never equal to that suffix.
    typedef_spelling_targets = _typedef_spelling_targets(
        dict(snapshot.typedefs), non_stdlib_identities
    )
    # The raw candidate vocabulary, separate from the resolved index above:
    # an ambiguous spelling (two typedefs disagreeing) is dropped from
    # `typedef_spelling_targets` rather than resolved, but it is exactly as
    # untrustworthy as a resolved, disagreeing one for this collision check.
    typedef_candidate_spellings = _typedef_candidate_spellings(
        dict(snapshot.typedefs), non_stdlib_identities
    )
    # Collision counts are computed over EVERY stdlib identity the snapshot
    # carries, not just the referenced subset -- an unreferenced sibling
    # sharing a referenced identity's bare spelling still makes that
    # spelling ambiguous for any other finding's bare Change.symbol to
    # match against. `set(...)` dedupes physical RecordType entries sharing
    # one identity (an ODR-duplicate/incomplete-declaration pair,
    # `_partition_snapshot_types`'s own list-per-identity reason) so a
    # duplicate doesn't inflate a count against itself.
    stripped_by_identity: dict[str, str] = {}
    stripped_counts: dict[str, int] = {}
    for identity in set(all_stdlib_identities):
        stripped = _stripped_signature_spelling(identity)
        if not stripped or stripped in non_stdlib_spellings:
            continue
        if stripped in typedef_candidate_spellings:
            typedef_target = typedef_spelling_targets.get(stripped)
            # A typedef target is never guaranteed to already be spelled in
            # this identity's own stripped bare form -- e.g. a target
            # spelled fully qualified (matching `identity` itself directly)
            # or, symmetrically, in some other stdlib-stripped shape -- so
            # compare against both the raw target and its own stripped form
            # before concluding a collision.
            normalized_target = (
                None
                if typedef_target is None
                else (_stripped_signature_spelling(typedef_target) or typedef_target)
            )
            if typedef_target != identity and normalized_target != stripped:
                # A real, unrelated typedef alias whose key -- or one of
                # its own derived namespace-suffix spellings -- happens to
                # equal this identity's stripped spelling (bare key,
                # namespace-qualified key, or an ambiguous one where
                # `typedef_spelling_targets` itself drops the spelling
                # rather than resolving it, leaving `typedef_target` as
                # `None`, still correctly rejected here) -- the stripped
                # spelling isn't this stdlib identity's own bare backend
                # form at all, it's someone else's (possibly ambiguous)
                # alias, so it can never safely stand in for the identity
                # in a bare Change.symbol match.
                continue
        stripped_by_identity[identity] = stripped
        stripped_counts[stripped] = stripped_counts.get(stripped, 0) + 1
    spellings: set[str] = set()
    for identity in identities:
        stripped = stripped_by_identity.get(identity)
        if stripped is None or stripped_counts[stripped] > 1:
            # The derived spelling either collides with an unrelated
            # non-stdlib record/enum (`stripped is None`, filtered out
            # above) or with another stdlib identity in the snapshot --
            # referenced or not (`stripped_counts[stripped] > 1`) -- either
            # way, only an identity independently proven via its own
            # literal spelling survives; the derived form itself is never
            # added here.
            if identity in exact:
                spellings.add(identity)
            continue
        if identity not in trusted:
            # The stripped form is unambiguous against everything else in
            # the snapshot -- but that alone only says "nothing else could
            # this spelling mean," not "the signature legitimately reaches
            # this spelling at all." An identity whose own reachability was
            # never proven trustworthy (e.g. found only inside a record
            # that was itself reached solely through an ambiguous typedef
            # alias) cannot be rescued by bare-spelling uniqueness alone.
            # No fallback to `exact` here (unlike the ambiguous-stripped-
            # form branch above): `exact` is always a subset of `trusted`
            # by construction (every site that adds to `_referenced_exact`/
            # `_exact_typedef_aliases` adds the identical identity/origin to
            # `_referenced_trusted`/`_trusted_via_alias` first), so
            # `identity not in trusted` already implies `identity not in
            # exact` -- nothing would ever survive here.
            continue
        # Unambiguous against every stdlib identity in the snapshot *and*
        # against every non-stdlib spelling in it, *and* independently
        # proven trustworthy: keep both the identity and its derived bare
        # form.
        spellings.add(identity)
        spellings.add(stripped)
    return frozenset(spellings)
