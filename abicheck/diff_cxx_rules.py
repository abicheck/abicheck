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

_ASCII_DIGITS = "0123456789"


def _read_length_prefixed_name(s: str, i: int) -> tuple[str | None, int]:
    """Read a ``<len><identifier>`` source-name at ``s[i]``.

    Returns ``(name, next_index)`` or ``(None, i)`` if malformed. Only ASCII
    digits count as the length prefix — Python's ``str.isdigit()`` also accepts
    Unicode digits (e.g. ``²``) that ``int()`` then rejects, so a fuzzed symbol
    must not be allowed to reach ``int()`` with a non-ASCII digit.
    """
    j = i
    while j < len(s) and s[j] in _ASCII_DIGITS:
        j += 1
    if j == i:
        return None, i
    n = int(s[i:j])
    name = s[j : j + n]
    if len(name) != n:
        return None, i  # truncated / malformed
    return name, j + n


def _skip_template_args(s: str, i: int) -> int | None:
    """``s[i] == 'I'``: return the index past the matching ``E``, or ``None``.

    Tracks nested template-argument (``I``) and nested-name (``N``) openers so
    the inner ``E`` of e.g. ``Box<ns::T>`` does not close the outer list early,
    and skips length-prefixed names so their literal ``I``/``N``/``E`` letters
    are not miscounted. Pathological encodings (e.g. substitutions whose
    base-36 index contains ``E``) may mis-balance; the caller treats ``None`` as
    "unparseable" and falls back, so a wrong guess never produces a finding.
    """
    depth = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c in _ASCII_DIGITS:
            name, i = _read_length_prefixed_name(s, i)
            if name is None:
                return None
            continue
        if c in ("I", "N"):
            depth += 1
            i += 1
        elif c == "E":
            depth -= 1
            i += 1
            if depth == 0:
                return i
        else:
            i += 1  # builtin type, qualifier, or substitution character
    return None


def itanium_scope_components(mangled: str) -> list[str] | None:
    """Scope components of an Itanium-mangled C++ symbol, parsed structurally.

    Decoding the nested-name encoding directly avoids any dependency on an
    external demangler (``c++filt`` / ``cxxfilt``), which is not installed on
    every platform — so this works identically on Linux, macOS, and Windows and
    never shells out. Handles the common length-prefixed forms, including
    class-template specializations (the raw template-argument encoding is kept so
    distinct specializations stay distinct)::

        _Z4drawi                       -> ["draw"]                  (free function)
        _ZN1C3barEv                    -> ["C", "bar"]              (member)
        _ZNK1C3barEv                   -> ["C", "bar"]              (const member)
        _ZN3lib12experimental4sortEv   -> ["lib", "experimental", "sort"]
        _ZN3BoxIiE4sizeEv              -> ["BoxIiE", "size"]        (Box<int>::size)

    Returns ``None`` for forms it does not model (constructors/operators,
    substitutions, non-Itanium or unmangled names) so callers fall back.
    """
    if not mangled.startswith("_Z"):
        return None
    s = mangled[2:]
    nested = s.startswith("N")
    if nested:
        s = s[1:]
        # Skip CV-qualifiers (r/V/K) and ref-qualifiers (R/O) on the implicit
        # object parameter, e.g. NK… (const), NR… (lvalue &), NO… (rvalue &&).
        while s[:1] in ("r", "V", "K", "R", "O"):
            s = s[1:]
    components: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if nested and c == "E":
            break
        if c in _ASCII_DIGITS:
            name, i = _read_length_prefixed_name(s, i)
            if name is None:
                return None
            # A directly-attached template-argument list belongs to this
            # component; keep it raw so Box<int> and Box<float> stay distinct.
            if i < n and s[i] == "I":
                end = _skip_template_args(s, i)
                if end is None:
                    return None
                name = name + s[i:end]
                i = end
            components.append(name)
        elif c == "C" and i + 1 < n and s[i + 1] in "12345":
            components.append("{ctor}")  # constructor (C1/C2/C3/…)
            i += 2
        elif c == "D" and i + 1 < n and s[i + 1] in "012345":
            components.append("{dtor}")  # destructor (D0/D1/D2/…)
            i += 2
        else:
            return None  # operator / substitution — not modelled
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


def _resolve_owner_type(
    owner: str, types: dict[str, RecordType], known_owners: set[str]
) -> RecordType | None:
    """Look up the owner's record, tolerating qualified-vs-leaf naming.

    DWARF records a class under its qualified name (``kde::View``); the CastXML
    dumper records it under the leaf (``View``). The owner derived from a mangled
    symbol is always qualified, so when the qualified key misses, fall back to
    the leaf component — but only when ``owner`` is a *known* qualified owner
    (i.e. the old surface actually had a symbol scoped to it). Without that
    corroboration a bare-leaf match could wrongly attach a brand-new
    ``kde::View`` to an unrelated existing ``foo::View`` that the dumper also
    recorded as ``View``.
    """
    t = types.get(owner)
    if t is not None:
        return t
    if owner not in known_owners:
        return None
    leaf = owner.rsplit("::", 1)[-1]
    return types.get(leaf) if leaf != owner else None


def virtual_method_addition(
    f_new: Function,
    old_owner_classes: set[str],
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

    The owner's record must be present on both sides (the DWARF blind spot this
    targets). ``old_owner_classes`` — the set of *scope-qualified* owners of the
    old snapshot's public functions — authorizes the leaf-name fallback in
    ``_resolve_owner_type``: a qualified owner (``kde::View``) is unambiguous,
    but CastXML record names are leaf-only, so a bare-leaf match is only trusted
    when a sibling symbol confirms that exact qualified owner existed before.
    """
    if not f_new.is_virtual:
        return None
    owner = owner_class_of(f_new)
    if owner is None:
        return None
    t_old = _resolve_owner_type(owner, old_types, old_owner_classes)
    t_new = _resolve_owner_type(owner, new_types, old_owner_classes)
    if t_old is None or t_new is None:
        return None  # no pre-existing record on both sides → compatible / out of scope
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
