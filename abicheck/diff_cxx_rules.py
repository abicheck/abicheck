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

from collections.abc import Iterable

from .checker_policy import ChangeKind
from .checker_types import Change
from .model import Function, RecordType

_ASCII_DIGITS = "0123456789"

# Fixed Itanium operator-function codes (a leaf, like a source-name). Used so
# operator overloads group (e.g. `operator[](int)` / `operator[](long)` both
# `ix`). Deliberately excludes `cv` (conversion-to-T — carries a type and is not
# an overload of other conversions) and variable forms (`li` literal, vendor).
_ITANIUM_OPERATORS = frozenset({
    "nw", "na", "dl", "da", "ng", "ad", "de", "co", "pl", "mi", "ml", "dv",
    "rm", "an", "or", "eo", "aS", "pL", "mI", "mL", "dV", "rM", "aN", "oR",
    "eO", "ls", "rs", "lS", "rS", "eq", "ne", "lt", "gt", "le", "ge", "ss",
    "nt", "aa", "oo", "pp", "mm", "cm", "pm", "pt", "cl", "ix", "qu", "aw",
})


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
    skips length-prefixed names so their literal ``I``/``N``/``E`` letters are
    not miscounted, and consumes ``L<type><value>E`` literal operands as a unit
    (non-type template args, e.g. ``Array<4>`` → ``ILi4EE``) so their value
    digits aren't read as a length and their closing ``E`` isn't counted.
    Pathological encodings (e.g. substitutions whose base-36 index contains
    ``E``) may mis-balance; the caller treats ``None`` as "unparseable" and falls
    back, so a wrong guess never produces a finding.
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
        if c == "L":
            # Literal operand `L <type> <value> E` — consume through its own
            # terminating E (literal values never contain an uppercase E).
            close = s.find("E", i + 1)
            if close == -1:
                return None
            i = close + 1
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
            # GNU ABI tags (`B<source-name>`, e.g. the libstdc++ `cxx11` tag on
            # std::string returns) are part of the unqualified-name identity;
            # keep them so a tagged name groups with itself across overloads.
            while i < n and s[i] == "B":
                tag, j = _read_length_prefixed_name(s, i + 1)
                if tag is None:
                    break
                name = f"{name}B{tag}"
                i = j
            components.append(name)
        elif c == "C" and i + 1 < n and s[i + 1] in "12345":
            components.append("{ctor}")  # constructor (C1/C2/C3/…)
            i += 2
        elif c == "D" and i + 1 < n and s[i + 1] in "012345":
            components.append("{dtor}")  # destructor (D0/D1/D2/…)
            i += 2
        elif s[i : i + 2] in _ITANIUM_OPERATORS:
            # Operator function: a fixed 2-char code (`ix`=[], `cl`=(), `pl`=+ …)
            # rather than a length-prefixed name. Keep the code so operator
            # overloads group (e.g. operator[](int)/(long)) while distinct
            # operators stay distinct. Conversion operators (`cv`) are excluded —
            # they carry a target type and are not overloads of each other.
            components.append(f"{{op:{s[i:i + 2]}}}")
            i += 2
        else:
            return None  # conversion operator / substitution / vendor — not modelled
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


def virtual_signature_key(f: Function) -> str:
    """A signature identity for a (virtual) method: ``leaf(params)cv-ref``.

    Two methods share a vtable slot only when their name *and* signature match
    (covariant return aside), so this — not the bare leaf — is what tells an
    inherited-virtual *override* (reuses a slot) apart from a same-named virtual
    with different parameters (adds a slot). Parameter types are compared by
    their recorded spelling, which is sufficient here.
    """
    leaf = f.name.rsplit("::", 1)[-1]
    params = ",".join(p.type for p in f.params)
    quals = ("c" if f.is_const else "") + ("v" if f.is_volatile else "") + f.ref_qualifier
    return f"{leaf}({params}){quals}"


def old_virtual_signatures(functions: Iterable[Function]) -> dict[str, set[str]]:
    """Per-class virtual-method signature keys for override detection.

    Keyed by *both* the qualified owner and its leaf, so a base name in either
    form (DWARF qualified, CastXML leaf) resolves. See ``virtual_method_addition``.
    """
    sigs: dict[str, set[str]] = {}
    for f in functions:
        if not f.is_virtual:
            continue
        owner = owner_class_of(f)
        if owner is None:
            continue
        sig = virtual_signature_key(f)
        sigs.setdefault(owner, set()).add(sig)
        leaf = owner.rsplit("::", 1)[-1]
        if leaf != owner:
            sigs.setdefault(leaf, set()).add(sig)
    return sigs


def _transitive_bases(start: RecordType | None, types: dict[str, RecordType]) -> set[str]:
    """All (transitive) base-class names reachable from record ``start``.

    Walks ``bases`` / ``virtual_bases``, resolving each base name through the
    record map with a leaf-name fallback (CastXML records and base names are
    leaf-only, while DWARF uses qualified names). Tolerant of missing records.
    """
    seen: set[str] = set()
    if start is None:
        return seen
    stack = [*start.bases, *start.virtual_bases]
    while stack:
        b = stack.pop()
        if b in seen:
            continue
        seen.add(b)
        rec = types.get(b) or types.get(b.rsplit("::", 1)[-1])
        if rec is not None:
            stack.extend((*rec.bases, *rec.virtual_bases))
    return seen


def virtual_method_addition(
    f_new: Function,
    old_owner_classes: set[str],
    old_types: dict[str, RecordType],
    new_types: dict[str, RecordType],
    old_virtual_sigs: dict[str, set[str]],
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

    ``old_virtual_sigs`` maps each class (keyed by both its qualified name and
    its leaf) to the *signature keys* of its virtual methods in the *old*
    snapshot; it is used to skip *overrides* of an inherited virtual (e.g.
    ``Derived::paint() override``), which reuse the base's existing vtable slot
    rather than adding one — those are ABI-compatible, not breaks. Matching is by
    full signature, so a same-named virtual with *different* parameters (a new
    slot, e.g. ``Derived::paint(double)`` over ``Base::paint(int)``) still fires.
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
    # An override of an inherited virtual reuses that base's slot — no new slot,
    # no relayout. If any transitive base already declared a virtual with the
    # *same signature* (not just the same name), treat the addition as an
    # override and stay silent; a different-signature same-name virtual is a new
    # slot and still fires.
    sig = virtual_signature_key(f_new)
    bases = _transitive_bases(t_new, new_types) | _transitive_bases(t_old, old_types)
    if any(sig in old_virtual_sigs.get(b, ()) for b in bases):
        return None
    return Change(
        kind=ChangeKind.VIRTUAL_METHOD_ADDED,
        symbol=f_new.mangled,
        description=(
            f"New virtual method added to existing class {owner}: {f_new.name} "
            "— grows/relayouts the vtable, breaking derived classes and old binaries"
        ),
        new_value=f_new.name,
    )
