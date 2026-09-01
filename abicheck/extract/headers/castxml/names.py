# Copyright 2026 Nikolay Petrov
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

"""Vtable-index, mangled-name, and synthetic-key helpers for the castxml
parser.

Split out of ``dumper_castxml.py`` (ADR-061 Phase 5 item 1: "split CastXML
and Clang parsing by entity and shared parser context"). Every function here
is a pure computation over a string or a single ``xml.etree`` element — none
of it reads the id map or any other state ``_CastxmlParser`` builds, so it
needed no shared parser context to become its own module. Re-exported from
``abicheck.dumper_castxml`` so existing imports keep working.
"""

from __future__ import annotations

import re
from typing import Any

from ....model.synthetic_key import (
    _SYNTHETIC_DTOR_KEY_PREFIX as _SYNTHETIC_DTOR_KEY_PREFIX,
    SYNTHETIC_CTOR_KEY_PREFIX as SYNTHETIC_CTOR_KEY_PREFIX,
    is_synthetic_ctor_key as is_synthetic_ctor_key,
    is_synthetic_dtor_key as is_synthetic_dtor_key,
)


def _parse_vtable_index(vi_str: str | None) -> int | None:
    """Parse vtable_index attribute, returning None for missing/invalid values."""
    if vi_str is None:
        return None
    stripped = vi_str.lstrip("-")
    return int(vi_str) if stripped.isdigit() else None


def _vt_sort_key(item: tuple[int | None, str]) -> tuple[int, int]:
    vi, _ = item
    return (0, vi) if vi is not None else (1, 0)


# Itanium <nested-name> ::= N [<CV-qualifiers: r/V/K>] [<ref-qualifier: R|O>] …
# At this position an uppercase R/O is unambiguous: prefix components start
# with a digit (source-name), S (substitution), T (template param), or a
# lowercase operator code — never a bare R/O.
_MANGLED_REF_QUAL = re.compile(r"^_ZN[rVK]*([RO])")


def _ref_qualifier_from_mangled(mangled: str) -> str:
    """Recover a member function's &/&& ref-qualifier from its Itanium mangling."""
    m = _MANGLED_REF_QUAL.match(mangled)
    if m is None:
        return ""
    return "&" if m.group(1) == "R" else "&&"


_MANGLED_SOURCE_NAME = re.compile(r"\d+")


def _mangled_name_is_local_linkage(mangled: str) -> bool:
    """Detect the Itanium ``<local-name>``/internal-linkage marker: a bare
    ``L`` immediately before the final component's length-prefixed
    source-name (e.g. ``_ZN5mylibL12hidden_constE`` for a non-``extern``
    namespace-scope ``const``/``constexpr`` variable).

    Parses the length-prefixed identifier chain component-by-component
    (jumping exactly ``length`` characters per source-name) rather than
    substring-matching for a literal ``L`` — a namespace or class name that
    merely *ends* in the letter ``L`` (e.g. ``MODEL``) is consumed as a whole
    source-name and never mistaken for the marker, since the parser always
    re-synchronizes on the next length-prefix digit run rather than rescanning
    already-consumed identifier characters.

    Returns ``False`` (not detected as local) on anything this simple
    single-source-name walker doesn't recognize (templates, operators, …) —
    a safe default, since the caller only uses this to rule OUT a public-CPO
    fallback, not to affirmatively hide something.
    """
    if not mangled.startswith("_Z"):
        return False
    i = 2
    n = len(mangled)
    if i < n and mangled[i] == "N":
        i += 1
    while i < n:
        local = mangled[i] == "L"
        if local:
            i += 1
        m = _MANGLED_SOURCE_NAME.match(mangled, i)
        if not m:
            return False
        length = int(m.group())
        i = m.end() + length
        if i > n:
            return False
        if local:
            return True
        if i < n and mangled[i] == "E":
            return False
    return False


def _virtual_method_mangled_name(method_el: Any) -> str:
    """A virtual method's mangled name, with the destructor fallback.

    castxml ``<Destructor>`` elements carry no ``mangled`` attribute. Without a
    fallback every virtual destructor is silently dropped from the vtable,
    which makes each polymorphic type look like it lacks a destructor slot
    (false ``POLYMORPHIC_TYPE_NON_VIRTUAL_DTOR``). The ``name`` attribute is the
    class name, so ``"~Name"`` is a stable, per-class entry.
    """
    mangled_name: str = method_el.get("mangled", "")
    if not mangled_name and method_el.tag == "Destructor":
        name = method_el.get("name", "")
        mangled_name = f"~{name}" if name else ""
    return mangled_name
