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

"""C++-specific ABI-rule helpers shared by the symbol/type diff passes.

Kept as a leaf module (depending only on the data model and result types) so
``diff_symbols`` can import it without creating an import cycle.
"""

from __future__ import annotations

from .checker_policy import ChangeKind
from .checker_types import Change
from .model import Function, RecordType


def itanium_scope_components(mangled: str) -> list[str] | None:
    """Scope components of an Itanium-mangled C++ symbol, parsed structurally.

    Decoding the nested-name encoding directly avoids any dependency on an
    external demangler (``c++filt`` / ``cxxfilt``), which is not installed on
    every platform — so this works identically on Linux, macOS, and Windows and
    never shells out. Handles the common length-prefixed forms::

        _Z4drawi                       -> ["draw"]                  (free function)
        _ZN1C3barEv                    -> ["C", "bar"]              (member)
        _ZNK1C3barEv                   -> ["C", "bar"]              (const member)
        _ZN3lib12experimental4sortEv   -> ["lib", "experimental", "sort"]

    Returns ``None`` for forms it does not model (templates, substitutions,
    constructors/operators, non-Itanium or unmangled names) so callers fall back
    to the display name.
    """
    if not mangled.startswith("_Z"):
        return None
    s = mangled[2:]
    nested = s.startswith("N")
    if nested:
        s = s[1:]
        # Skip CV-/ref-qualifiers on the implicit object parameter (e.g. NK / NV).
        while s[:1] in ("r", "V", "K"):
            s = s[1:]
    components: list[str] = []
    while s:
        if nested and s[0] == "E":
            break
        if not s[0].isdigit():
            return None  # operator / ctor / template / substitution — not modelled
        j = 0
        while j < len(s) and s[j].isdigit():
            j += 1
        n = int(s[:j])
        name = s[j : j + n]
        if len(name) != n:
            return None  # truncated / malformed
        components.append(name)
        s = s[j + n :]
        if not nested:
            break  # free function: one component, the rest is the parameter encoding
    return components or None


def itanium_qualified_name(mangled: str) -> str | None:
    """Fully scope-qualified name (``ns::C::bar``) from a mangled symbol, or None."""
    comps = itanium_scope_components(mangled)
    return "::".join(comps) if comps else None


def owner_class_of(f: Function) -> str | None:
    """The enclosing class/struct of a method.

    Prefer the (already scope-qualified) display name; fall back to the mangled
    name when the dumper recorded an unqualified leaf (CastXML records the bare
    ``bar`` rather than ``C::bar``). ``Foo::bar`` → ``Foo``;
    ``ns::Foo::bar`` → ``ns::Foo``; a free function → ``None``.
    """
    if "::" in f.name:
        return f.name.rsplit("::", 1)[0]
    comps = itanium_scope_components(f.mangled)
    if not comps or len(comps) < 2:
        return None
    return "::".join(comps[:-1])


def virtual_method_addition(
    f_new: Function,
    old_types: dict[str, RecordType],
    new_types: dict[str, RecordType],
) -> Change | None:
    """A new *virtual* method on a class that already exists across versions.

    Returns a ``VIRTUAL_METHOD_ADDED`` change, or ``None`` if this added symbol
    is not a virtual added to a pre-existing type. Scoped to the genuine blind
    spot: when the owner's ``vtable`` array is identical on both sides (e.g.
    DWARF/symbol-only snapshots that carry no vtable layout), the per-type
    ``TYPE_VTABLE_CHANGED`` detector cannot see the growth, so this is the only
    signal. When the vtable array *does* differ, ``TYPE_VTABLE_CHANGED`` already
    reports it and we defer to avoid a duplicate finding.
    """
    if not f_new.is_virtual:
        return None
    owner = owner_class_of(f_new)
    if owner is None:
        return None
    t_old = old_types.get(owner)
    t_new = new_types.get(owner)
    if t_old is None or t_new is None:
        return None  # brand-new class → adding it (with virtuals) is compatible
    if t_old.vtable != t_new.vtable:
        return None  # TYPE_VTABLE_CHANGED covers this case
    return Change(
        kind=ChangeKind.VIRTUAL_METHOD_ADDED,
        symbol=f_new.mangled,
        description=(
            f"New virtual method added to existing class {owner}: {f_new.name} "
            "— grows/relayouts the vtable, breaking derived classes and old binaries"
        ),
        new_value=f_new.name,
    )
