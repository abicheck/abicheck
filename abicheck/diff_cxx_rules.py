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
from .demangle import demangle
from .model import Function, RecordType


def _scope_before_last_separator(s: str) -> str | None:
    """Everything before the last ``::`` at template depth 0, or ``None``.

    ``ns::Foo::bar`` → ``ns::Foo``; ``bar`` → ``None``. Template-depth aware so
    ``ns::Foo<a::b>`` is not split inside its argument list.
    """
    depth = 0
    last = -1
    i = 0
    while i < len(s) - 1:
        ch = s[i]
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif ch == ":" and s[i + 1] == ":" and depth == 0:
            last = i
            i += 2
            continue
        i += 1
    return s[:last] if last != -1 else None


def _owner_from_mangled(mangled: str) -> str | None:
    """Resolve a method's enclosing class/struct from its mangled name.

    Needed for header/CastXML snapshots, which record ``Function.name`` without
    namespace/class scope (``bar`` rather than ``C::bar``), so the display name
    alone cannot identify the owner. Demangle, drop the parameter list, and take
    the scope before the leaf. Returns ``None`` when the symbol is not a scoped
    C++ name (free function, C symbol, or no demangler available).
    """
    demangled = demangle(mangled)
    if demangled is None:
        return None
    depth = 0
    head = demangled
    for i, ch in enumerate(demangled):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif ch == "(" and depth == 0:
            head = demangled[:i]
            break
    return _scope_before_last_separator(head.strip())


def owner_class_of(f: Function) -> str | None:
    """The enclosing class/struct of a method.

    Prefer the (already scope-qualified) display name; fall back to demangling
    the mangled name when the dumper recorded an unqualified leaf (CastXML).
    ``Foo::bar`` → ``Foo``; ``ns::Foo::bar`` → ``ns::Foo``; a free function
    → ``None``.
    """
    if "::" in f.name:
        return f.name.rsplit("::", 1)[0]
    return _owner_from_mangled(f.mangled)


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
