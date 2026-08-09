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
    stdlib_identities: list[str], non_stdlib_identities: frozenset[str]
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
    """
    non_stdlib_spellings = _non_stdlib_signature_spellings(non_stdlib_identities)
    stdlib_index: dict[str, set[str]] = {}
    for identity in stdlib_identities:
        stdlib_index.setdefault(identity, set()).add(identity)
        stripped = _stripped_signature_spelling(identity)
        if stripped is not None and stripped not in non_stdlib_spellings:
            stdlib_index.setdefault(stripped, set()).add(identity)

    record_index: dict[str, set[str]] = {}
    generic_bare: dict[str, set[str]] = {}
    for identity in non_stdlib_identities:
        record_index.setdefault(identity, set()).add(identity)
        for suffix in _namespace_suffix_spellings(identity)[1:]:
            generic_bare.setdefault(suffix, set()).add(identity)
    for bare, ids in generic_bare.items():
        if bare in non_stdlib_identities:
            # A derived suffix that collides with a *different* record's own
            # full identity (Codex review, fresh evidence: identities "Inner"
            # and "api::Inner" both present) is ambiguous the same way two
            # colliding derived suffixes are. Removing the spelling entirely
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
    decl: Function | Variable, *, exclude_export_only: bool = False
) -> bool:
    """True when *decl* may seed the scan as a public reachability root.

    Four independent reasons a retained declaration must not be treated as a
    public reachability root, applied identically to both kinds — see
    :func:`directly_referenced_stdlib_types`'s own docstring for why each one
    matters:

    * its display name is stdlib-namespaced;
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
    ) -> None:
        self._stdlib_index, self._record_index = _spelling_index(
            stdlib_identities, non_stdlib_identities
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
        self._remaining = set(stdlib_identities)
        self._reached_records: set[str] = set()
        self._worklist: list[str] = []
        self._resolved_typedefs: set[str] = set()

    @property
    def exhausted(self) -> bool:
        """True once every stdlib candidate has been accounted for.

        Both seeding loops stop early on this: there is nothing left to find.
        """
        return not self._remaining

    def scan(self, type_string: str) -> None:
        """Record every stdlib/non-stdlib identity *type_string* names;
        newly-reached non-stdlib records are queued for their own fields to
        be walked in turn. Also follows a typedef alias to its own target
        (Codex review, fresh evidence: ``surface.py``'s own reachability
        closure does the same), so a public signature spelled with a
        user-defined alias name still reaches the record it actually
        names."""
        if not type_string:
            return
        for match in _finditer_allow_nested(self._stdlib_pattern, type_string):
            for identity in self._stdlib_index.get(match.group(0), ()):
                if identity in self._remaining:
                    self._referenced.add(identity)
                    self._remaining.discard(identity)
        if self._record_pattern is not None:
            for match in _finditer_allow_nested(self._record_pattern, type_string):
                for identity in self._record_index.get(match.group(0), ()):
                    self.reach_record(identity)
        if self._typedef_pattern is not None:
            for match in _finditer_allow_nested(self._typedef_pattern, type_string):
                alias = match.group(0)
                if alias not in self._resolved_typedefs:
                    self._resolved_typedefs.add(alias)
                    self.scan(self._typedef_targets[alias])

    def reach_record(self, identity: str) -> None:
        """Queue a non-stdlib record's own fields/bases to be walked, once."""
        if identity not in self._reached_records:
            self._reached_records.add(identity)
            self._worklist.append(identity)

    def next_reached_record(self) -> str | None:
        """Pop the next queued record identity, or ``None`` when the queue
        is empty."""
        return self._worklist.pop() if self._worklist else None

    def referenced(self) -> frozenset[str]:
        """The stdlib identities this walk proved directly referenced."""
        return frozenset(self._referenced)


def _seed_scan_from_public_declarations(
    snapshot: AbiSnapshot,
    scan: _StdlibReferenceScan,
    non_stdlib_identities: frozenset[str],
    *,
    exclude_export_only: bool = False,
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

    ``exclude_export_only`` is forwarded to
    :func:`_is_public_non_stdlib_declaration` unchanged — see its own
    docstring.
    """
    for fn in snapshot.functions:
        if scan.exhausted:
            break
        if not _is_public_non_stdlib_declaration(
            fn, exclude_export_only=exclude_export_only
        ):
            continue
        scan.scan(fn.return_type)
        for param in fn.params:
            scan.scan(param.type)
        owner = owner_class_of(fn)
        if owner is not None and owner in non_stdlib_identities:
            scan.reach_record(owner)

    for var in snapshot.variables:
        if scan.exhausted:
            break
        if not _is_public_non_stdlib_declaration(
            var, exclude_export_only=exclude_export_only
        ):
            continue
        scan.scan(var.type)


def _walk_reached_records(
    scan: _StdlibReferenceScan,
    non_stdlib_records: dict[str, list[RecordType]],
    *,
    exclude_export_only: bool = False,
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
    """
    while not scan.exhausted:
        identity = scan.next_reached_record()
        if identity is None:
            return
        for rec in non_stdlib_records[identity]:
            if rec.origin in _NON_PUBLIC_ORIGINS:
                continue
            if exclude_export_only and rec.origin is ScopeOrigin.EXPORT_ONLY:
                continue
            for f in rec.fields:
                scan.scan(f.type)
            # Both direct and virtual bases are ABI-reachable through the
            # derived type (virtual inheritance still embeds the base
            # subobject + vtable path), same as surface.py's own closure
            # (Codex review, fresh evidence): a public Derived inheriting a
            # non-stdlib Base whose own field is a stdlib record was
            # otherwise never reached, since only rec.fields was followed.
            for base in (*rec.bases, *rec.virtual_bases):
                scan.scan(base)


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
    stdlib_identities, non_stdlib_identities, non_stdlib_records = (
        _partition_snapshot_types(snapshot)
    )
    if not stdlib_identities:
        return frozenset()

    scan = _StdlibReferenceScan(
        stdlib_identities, non_stdlib_identities, dict(snapshot.typedefs)
    )
    _seed_scan_from_public_declarations(
        snapshot,
        scan,
        non_stdlib_identities,
        exclude_export_only=exclude_export_only_roots,
    )
    _walk_reached_records(
        scan, non_stdlib_records, exclude_export_only=exclude_export_only_roots
    )
    return scan.referenced()


def directly_referenced_stdlib_type_spellings(
    snapshot: AbiSnapshot, *, exclude_export_only_roots: bool = False
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

    **Known conservative gap, deliberately not attempted here:** grouping
    is keyed purely on whether an identity's stripped spelling collides
    with another *referenced* identity's -- it cannot distinguish "reached
    only via the ambiguous shared spelling" from "also independently
    reached via its own unique full spelling elsewhere in the same
    snapshot". A whole ambiguous group is excluded even when one member is
    separately, unambiguously confirmable another way, which is a real but
    rare false negative. Recovering that distinction needs per-match-route
    provenance from the underlying scan (:class:`_StdlibReferenceScan`),
    which today only returns a flat set of referenced identities with no
    record of *which* match(es) produced each one -- a deeper change than
    this collision-guard fix. Same false-negative-over-false-positive
    direction this whole module already commits to throughout.

    ``exclude_export_only_roots`` is forwarded to
    ``directly_referenced_stdlib_types`` unchanged (Codex review, fresh
    evidence): contract evaluation's own public-header-domain use must set
    this, since an export-only declaration is exactly the evidence the
    separate ``exports`` domain exists to evaluate, not ``public``'s.
    """
    _, non_stdlib_identities, _ = _partition_snapshot_types(snapshot)
    non_stdlib_spellings = _non_stdlib_signature_spellings(non_stdlib_identities)
    referenced = directly_referenced_stdlib_types(
        snapshot, exclude_export_only_roots=exclude_export_only_roots
    )
    stripped_owners: dict[str, set[str]] = {}
    for identity in referenced:
        stripped = _stripped_signature_spelling(identity)
        if stripped is not None:
            stripped_owners.setdefault(stripped, set()).add(identity)
    spellings: set[str] = set()
    for identity in referenced:
        stripped = _stripped_signature_spelling(identity)
        if stripped is not None and len(stripped_owners[stripped]) > 1:
            # This identity's own presence in `referenced` is exactly as
            # unproven as its stripped spelling's ambiguity -- exporting
            # neither form avoids treating that shared, unconfirmed
            # evidence as proof about any one specific identity.
            continue
        spellings.add(identity)
        if stripped is not None and stripped not in non_stdlib_spellings:
            spellings.add(stripped)
    return frozenset(spellings)
