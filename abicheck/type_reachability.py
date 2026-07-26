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

from .diff_cxx_rules import itanium_qualified_name, owner_class_of
from .model import ScopeOrigin, Visibility
from .name_classification import STDLIB_TYPE_NAMESPACE_PREFIXES

if TYPE_CHECKING:
    from .model import AbiSnapshot, RecordType

__all__ = [
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
    """
    non_stdlib_spellings = _non_stdlib_signature_spellings(non_stdlib_identities)
    targets_by_spelling: dict[str, set[str]] = {}
    for key, target in typedefs.items():
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


def directly_referenced_stdlib_types(snapshot: AbiSnapshot) -> frozenset[str]:
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
    """
    stdlib_identities: list[str] = []
    non_stdlib_identities: set[str] = set()
    non_stdlib_records: dict[str, RecordType] = {}
    for t in snapshot.types:
        identity = _record_identity(t.name, t.qualified_name)
        if identity.startswith(STDLIB_TYPE_NAMESPACE_PREFIXES):
            stdlib_identities.append(identity)
        else:
            non_stdlib_identities.add(identity)
            non_stdlib_records[identity] = t
    if not stdlib_identities:
        return frozenset()

    stdlib_index, record_index = _spelling_index(
        stdlib_identities, frozenset(non_stdlib_identities)
    )
    stdlib_pattern = _compile_spelling_pattern(stdlib_index)
    record_pattern = _compile_spelling_pattern(record_index)
    # stdlib_index always has at least one entry here (every stdlib
    # identity maps at least itself), so _compile_spelling_pattern's
    # empty-input case never applies to this caller.
    assert stdlib_pattern is not None

    referenced: set[str] = set()
    remaining = set(stdlib_identities)
    reached_records: set[str] = set()
    worklist: list[str] = []

    typedef_targets = _typedef_spelling_targets(
        dict(snapshot.typedefs), frozenset(non_stdlib_identities)
    )
    typedef_pattern = (
        _compile_spelling_pattern(typedef_targets) if typedef_targets else None
    )
    resolved_typedefs: set[str] = set()

    def _scan(type_string: str) -> None:
        """Record every stdlib/non-stdlib identity *type_string* names;
        newly-reached non-stdlib records are queued for their own fields to
        be walked in turn. Also follows a typedef alias to its own target
        (Codex review, fresh evidence: ``surface.py``'s own reachability
        closure does the same), so a public signature spelled with a
        user-defined alias name still reaches the record it actually
        names."""
        if not type_string:
            return
        for match in stdlib_pattern.finditer(type_string):
            for identity in stdlib_index.get(match.group(0), ()):
                if identity in remaining:
                    referenced.add(identity)
                    remaining.discard(identity)
        if record_pattern is not None:
            for match in record_pattern.finditer(type_string):
                for identity in record_index.get(match.group(0), ()):
                    if identity not in reached_records:
                        reached_records.add(identity)
                        worklist.append(identity)
        if typedef_pattern is not None:
            for match in typedef_pattern.finditer(type_string):
                alias = match.group(0)
                if alias not in resolved_typedefs:
                    resolved_typedefs.add(alias)
                    _scan(typedef_targets[alias])

    for fn in snapshot.functions:
        if not remaining:
            break
        if fn.name.startswith(STDLIB_TYPE_NAMESPACE_PREFIXES):
            continue
        qualified_fn = itanium_qualified_name(fn.mangled)
        if qualified_fn is not None and qualified_fn.startswith(
            STDLIB_TYPE_NAMESPACE_PREFIXES
        ):
            continue
        if fn.visibility != Visibility.PUBLIC:
            continue
        if fn.origin in _NON_PUBLIC_ORIGINS:
            continue
        _scan(fn.return_type)
        for param in fn.params:
            _scan(param.type)
        owner = owner_class_of(fn)
        if (
            owner is not None
            and owner in non_stdlib_identities
            and owner not in reached_records
        ):
            reached_records.add(owner)
            worklist.append(owner)

    for var in snapshot.variables:
        if not remaining:
            break
        if var.name.startswith(STDLIB_TYPE_NAMESPACE_PREFIXES):
            continue
        qualified_var = itanium_qualified_name(var.mangled)
        if qualified_var is not None and qualified_var.startswith(
            STDLIB_TYPE_NAMESPACE_PREFIXES
        ):
            continue
        if var.visibility != Visibility.PUBLIC:
            continue
        if var.origin in _NON_PUBLIC_ORIGINS:
            continue
        _scan(var.type)

    while worklist and remaining:
        rec = non_stdlib_records[worklist.pop()]
        if rec.origin in _NON_PUBLIC_ORIGINS:
            continue
        for f in rec.fields:
            _scan(f.type)
        # Both direct and virtual bases are ABI-reachable through the
        # derived type (virtual inheritance still embeds the base
        # subobject + vtable path), same as surface.py's own closure
        # (Codex review, fresh evidence): a public Derived inheriting a
        # non-stdlib Base whose own field is a stdlib record was
        # otherwise never reached, since only rec.fields was followed.
        for base in (*rec.bases, *rec.virtual_bases):
            _scan(base)

    return frozenset(referenced)
