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

**Not yet wired into any live detector.** Retrofitting the ~15
``is_non_abi_surface_type``/``is_abi_surface_type_name`` call sites across
``diff_types.py``/``diff_platform.py``/``diff_symbols.py``/
``diff_vtable_layout.py``/``diff_stdlib_impl.py``/``diff_layout.py``/
``diff_filtering.py``/``diff_type_spellings.py`` to consult this needs each
site individually verified against the FP-rate/mutation-score gates (this
codebase's test-quality guards exist specifically to catch exactly this kind
of change going wrong) — a scoped follow-up, not a drive-by extension here
(status-review, CLAUDE.md "M1-6"-style deferred item).
"""

from __future__ import annotations

import re
from collections.abc import Collection
from typing import TYPE_CHECKING

from .diff_cxx_rules import owner_class_of
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
# "std::"-namespace prefix itself (Codex review, fresh evidence).
_LIBCXX_INLINE_NAMESPACE_MARKERS: tuple[str, ...] = ("__1::", "__ndk1::")

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
    ``std::__ndk1::vector<int>``) — invisible to real C++ code but very much
    present in the debug-info-derived qualified name, so it must be stripped
    too or the reconstructed spelling (``"__1::vector<int>"``) still never
    matches a bare backend signature (Codex review, fresh evidence).

    This does **not** close the deeper, separate gap of typedef-aliased
    stdlib types (``std::string``, ``std::wstring``, ...): a signature
    spelled ``std::string`` names the alias, not the real underlying class
    (``std::basic_string<char, ...>``) that owns the ``RecordType`` entry,
    and no current model field maps one back to the other. See
    ``AGENTS.md``'s "Known gaps" for why resolving that needs a dedicated
    typedef-alias-resolution layer, not a string-spelling fallback.
    """
    for prefix in STDLIB_TYPE_NAMESPACE_PREFIXES:
        if identity.startswith(prefix):
            rest = identity[len(prefix) :]
            for marker in _LIBCXX_INLINE_NAMESPACE_MARKERS:
                if rest.startswith(marker):
                    rest = rest[len(marker) :]
                    break
            return rest
    return None


def _spelling_index(
    stdlib_identities: list[str], non_stdlib_identities: frozenset[str]
) -> dict[str, frozenset[str]]:
    """spelling -> {identity, ...} that spelling proves reachable.

    Covers both stdlib candidates (the ultimate targets) and non-stdlib
    records (intermediate reachability-closure nodes — see
    :func:`directly_referenced_stdlib_types`'s worklist) in one combined
    index so a single compiled pattern can scan every declaration once.

    A stdlib candidate's stripped spelling that collides with a real,
    unrelated non-stdlib record's own identity is dropped (Codex review,
    fresh evidence): a library can happen to define its own public type
    with the exact bare spelling a stdlib candidate reduces to after
    stripping (e.g. its own top-level ``vector<int, ...>``), and a
    signature naming that unrelated user type must not be misread as a
    direct stdlib reference — silently missing that stdlib candidate here
    (a false negative) is far safer than attributing an unrelated type's
    layout change to it (a false positive). Multiple *stdlib* identities can
    legitimately share one spelling (e.g. two distinct namespaces both
    reducing to the same bare form) — every one of them is recorded, not
    just the first.

    A non-stdlib record's own bare-trailing-segment alias is dropped
    instead of recorded when it is ambiguous — shared by two or more
    *different* non-stdlib records (Codex review, fresh evidence: e.g.
    ``api::Inner`` and ``detail::Inner`` both reducing to bare ``Inner``):
    unlike the stdlib case above, queuing every colliding record here would
    let a signature naming one of them wrongly walk an unrelated internal
    record's fields too, misattributing its own implementation-only churn
    as publicly reachable. Each record's own full identity is never
    ambiguous this way and is always kept.
    """
    index: dict[str, set[str]] = {}
    for identity in stdlib_identities:
        index.setdefault(identity, set()).add(identity)
        stripped = _stripped_signature_spelling(identity)
        if stripped is not None and stripped not in non_stdlib_identities:
            index.setdefault(stripped, set()).add(identity)

    generic_bare: dict[str, set[str]] = {}
    for identity in non_stdlib_identities:
        index.setdefault(identity, set()).add(identity)
        if "::" in identity:
            bare = identity.rsplit("::", 1)[1]
            generic_bare.setdefault(bare, set()).add(identity)
    for bare, ids in generic_bare.items():
        if len(ids) == 1:
            index.setdefault(bare, set()).update(ids)
        # else: ambiguous bare alias shared by distinct records -- drop.

    return {spelling: frozenset(ids) for spelling, ids in index.items()}


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

    A non-stdlib record's own fields are only consulted once that record
    itself is confirmed reachable from a public root — by direct mention in
    a public function's own signature, by being that function's *owner*
    class/struct for a member function (Codex review, fresh evidence: a
    public method like ``void Foo::run()`` never repeats ``Foo`` in its own
    return/parameter types, so without also seeding
    :func:`abicheck.diff_cxx_rules.owner_class_of` the previous version
    never queued ``Foo`` at all — a genuine layout break in one of its
    fields would be silently missed), or transitively through another
    already-reachable record's fields (Codex review, fresh evidence: the
    previous version scanned *every* non-stdlib record's fields
    unconditionally, so a purely internal, never-actually-reachable record
    — e.g. one a DWARF-only snapshot retains with the default
    ``ScopeOrigin.UNKNOWN`` even though nothing public touches it — could
    still make an unrelated stdlib type look directly referenced). A
    record's own ``origin`` being ``PRIVATE_HEADER``/``SYSTEM_HEADER``/
    ``GENERATED`` still excludes its fields from the walk, same as before.
    See :func:`_spelling_index` for why an ambiguous bare alias shared by
    two distinct non-stdlib records (Codex review, fresh evidence) is
    dropped rather than queuing both.
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

    spelling_index = _spelling_index(
        stdlib_identities, frozenset(non_stdlib_identities)
    )
    pattern = _compile_spelling_pattern(spelling_index)
    # spelling_index always has at least one entry here (every stdlib
    # identity maps at least itself), so _compile_spelling_pattern's
    # empty-input case never applies to this caller.
    assert pattern is not None

    referenced: set[str] = set()
    remaining = set(stdlib_identities)
    reached_records: set[str] = set()
    worklist: list[str] = []

    typedef_targets: dict[str, str] = dict(snapshot.typedefs)
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
        for match in pattern.finditer(type_string):
            for identity in spelling_index.get(match.group(0), ()):
                if identity in remaining:
                    referenced.add(identity)
                    remaining.discard(identity)
                elif (
                    identity in non_stdlib_identities
                    and identity not in reached_records
                ):
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
        if fn.visibility != Visibility.PUBLIC:
            continue
        if fn.origin in _NON_PUBLIC_ORIGINS:
            continue
        _scan(fn.return_type)
        for param in fn.params:
            _scan(param.type)
        owner = owner_class_of(fn)
        if owner is not None:
            _scan(owner)

    for var in snapshot.variables:
        if not remaining:
            break
        if var.name.startswith(STDLIB_TYPE_NAMESPACE_PREFIXES):
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

    return frozenset(referenced)
