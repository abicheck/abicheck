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

from typing import TYPE_CHECKING

from .model import ScopeOrigin, Visibility
from .name_classification import STDLIB_TYPE_NAMESPACE_PREFIXES

if TYPE_CHECKING:
    from .model import AbiSnapshot

__all__ = [
    "directly_referenced_stdlib_types",
    "type_string_references_name",
]

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


def _signature_spellings(identity: str) -> frozenset[str]:
    """Every spelling of *identity* that a real dumper backend's own
    ``Function.return_type``/``Param.type``/``TypeField.type`` strings might
    actually use (Codex review, fresh evidence).

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
    even when ``RecordType.name`` is fully qualified. Returning both the
    full identity and its prefix-stripped form lets the scan match whichever
    convention the snapshot's producer actually used, without guessing which
    backend produced it.

    This does **not** close the deeper, separate gap of typedef-aliased
    stdlib types (``std::string``, ``std::wstring``, ...): a signature
    spelled ``std::string`` names the alias, not the real underlying class
    (``std::basic_string<char, ...>``) that owns the ``RecordType`` entry,
    and no current model field maps one back to the other. See
    ``AGENTS.md``'s "Known gaps" for why resolving that needs a dedicated
    typedef-alias-resolution layer, not a string-spelling fallback.
    """
    spellings = {identity}
    for prefix in STDLIB_TYPE_NAMESPACE_PREFIXES:
        if identity.startswith(prefix):
            spellings.add(identity[len(prefix) :])
    return frozenset(spellings)


def directly_referenced_stdlib_types(snapshot: AbiSnapshot) -> frozenset[str]:
    """Stdlib/runtime-namespaced :class:`RecordType` names in *snapshot* that
    are directly referenced by a **public**, non-stdlib function's
    return/parameter type or a non-stdlib :class:`RecordType`'s own field
    type.

    Returns the empty set when the snapshot carries no stdlib-namespaced
    types at all (the common case) — never an error. Deliberately a single,
    snapshot-scoped, pure computation: no build/source evidence, no template
    argument resolution beyond substring matching (see
    :func:`type_string_references_name`), so a stdlib type mentioned only
    inside another stdlib type's own template arguments (never surfacing in
    a non-stdlib declaration) is correctly excluded.

    Candidate identification uses ``qualified_name or name`` (Codex review,
    fresh evidence), not ``name`` alone: castxml/direct-clang record the bare
    leaf in ``name`` and the namespace-qualified spelling separately in
    ``qualified_name``, so ``name`` alone never carries a ``std::`` prefix
    for those two backends and this helper would silently find nothing. See
    :func:`_signature_spellings` for how the resulting identity is matched
    back against the (differently-spelled) signature type strings.

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
    is rejected here too, before its signature is ever scanned. The same
    ``origin`` check applies to the record-field scan below (Codex review,
    fresh evidence): ``RecordType`` carries the identical provenance axis,
    and a non-stdlib record retained only from a private/system/generated
    header must not make its own field types count as reachability roots
    either.
    """
    candidates: dict[str, frozenset[str]] = {}
    for t in snapshot.types:
        identity = _record_identity(t.name, t.qualified_name)
        if identity.startswith(STDLIB_TYPE_NAMESPACE_PREFIXES):
            candidates[identity] = _signature_spellings(identity)
    if not candidates:
        return frozenset()

    referenced: set[str] = set()
    remaining = set(candidates)

    def _scan(type_string: str) -> None:
        if not remaining:
            return
        for identity in tuple(remaining):
            if any(
                type_string_references_name(type_string, spelling)
                for spelling in candidates[identity]
            ):
                referenced.add(identity)
                remaining.discard(identity)

    for fn in snapshot.functions:
        if fn.name.startswith(STDLIB_TYPE_NAMESPACE_PREFIXES):
            continue
        if fn.visibility != Visibility.PUBLIC:
            continue
        if fn.origin in _NON_PUBLIC_ORIGINS:
            continue
        _scan(fn.return_type)
        for param in fn.params:
            _scan(param.type)
        if not remaining:
            break

    if remaining:
        for rec in snapshot.types:
            if _record_identity(rec.name, rec.qualified_name).startswith(
                STDLIB_TYPE_NAMESPACE_PREFIXES
            ):
                continue
            if rec.origin in _NON_PUBLIC_ORIGINS:
                continue
            for f in rec.fields:
                _scan(f.type)
            if not remaining:
                break

    return frozenset(referenced)
