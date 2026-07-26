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

from .name_classification import STDLIB_TYPE_NAMESPACE_PREFIXES

if TYPE_CHECKING:
    from .model import AbiSnapshot

__all__ = [
    "directly_referenced_stdlib_types",
    "type_string_references_name",
]


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


def directly_referenced_stdlib_types(snapshot: AbiSnapshot) -> frozenset[str]:
    """Stdlib/runtime-namespaced :class:`RecordType` names in *snapshot* that
    are directly referenced by a non-stdlib function's return/parameter type
    or a non-stdlib :class:`RecordType`'s own field type.

    Returns the empty set when the snapshot carries no stdlib-namespaced
    types at all (the common case) — never an error. Deliberately a single,
    snapshot-scoped, pure computation: no build/source evidence, no template
    argument resolution beyond substring matching (see
    :func:`type_string_references_name`), so a stdlib type mentioned only
    inside another stdlib type's own template arguments (never surfacing in
    a non-stdlib declaration) is correctly excluded.
    """
    stdlib_names = [
        t.name
        for t in snapshot.types
        if t.name.startswith(STDLIB_TYPE_NAMESPACE_PREFIXES)
    ]
    if not stdlib_names:
        return frozenset()

    referenced: set[str] = set()
    remaining = set(stdlib_names)

    def _scan(type_string: str) -> None:
        if not remaining:
            return
        for name in tuple(remaining):
            if type_string_references_name(type_string, name):
                referenced.add(name)
                remaining.discard(name)

    for fn in snapshot.functions:
        if fn.name.startswith(STDLIB_TYPE_NAMESPACE_PREFIXES):
            continue
        _scan(fn.return_type)
        for param in fn.params:
            _scan(param.type)
        if not remaining:
            break

    if remaining:
        for rec in snapshot.types:
            if rec.name.startswith(STDLIB_TYPE_NAMESPACE_PREFIXES):
                continue
            for f in rec.fields:
                _scan(f.type)
            if not remaining:
                break

    return frozenset(referenced)
