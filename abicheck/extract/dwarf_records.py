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

"""Small, dependency-free DWARF record-type helpers, shared out of
``dwarf_snapshot.py`` (ADR-061: ``dwarf_snapshot.py`` is a legacy root
module still migrating its responsibilities into ``extract/`` piece by
piece; these four free functions have no dependency on the builder's own
state, so they move first).

Leaf module: depends only on ``dwarf_utils`` and ``model`` (allowed:
``extract -> model``, ADR-061) -- nothing above.
"""

from __future__ import annotations

from typing import Any

from ..dwarf_utils import (
    attr_bool as _attr_bool,
    attr_str as _attr_str,
    decode_member_location as _decode_member_location,
    resolve_type_die as _resolve_type_die,
)
from ..model import AccessLevel

__all__ = [
    "access_from_dwarf",
    "default_member_access_for_tag",
    "local_vptr_member_offset_bits",
    "record_kind_from_tag",
    "variable_is_const",
]


def record_kind_from_tag(tag: str) -> str:
    """Return the ABI kind string for a record-type DWARF tag.

    Maps ``DW_TAG_union_type`` → ``"union"``, ``DW_TAG_class_type`` →
    ``"class"``, and everything else (``DW_TAG_structure_type``) → ``"struct"``.
    """
    if tag == "DW_TAG_union_type":
        return "union"
    if tag == "DW_TAG_class_type":
        return "class"
    return "struct"


def default_member_access_for_tag(tag: str) -> AccessLevel:
    """C++'s default member access for a record-type DWARF tag.

    A compiler only emits ``DW_AT_accessibility`` on a member when it
    *differs* from the enclosing record's language default — ``class``
    defaults to private, ``struct``/``union`` to public (matching the C++
    access-specifier rules) — so an absent attribute must resolve to this,
    not unconditionally to public (Codex review: a `class`'s first,
    unlabelled member, or an anonymous aggregate declared before any access
    label, both carry no ``DW_AT_accessibility`` at all and were previously
    misread as public).
    """
    return AccessLevel.PRIVATE if tag == "DW_TAG_class_type" else AccessLevel.PUBLIC


def local_vptr_member_offset_bits(child: Any) -> int | None:
    """Bit offset of *child* if it is the compiler's artificial vptr member.

    GCC names it ``_vptr.<Class>``, Clang ``_vptr$<Class>`` — both emit it as
    an ``DW_TAG_member`` with ``DW_AT_artificial: true`` and a real
    ``DW_AT_data_member_location`` (verified against real GCC 13/Clang 18
    ``-g`` output). Returns ``None`` for any other member, including a
    non-artificial member and an artificial member that isn't the vptr (e.g.
    a compiler-synthesized default argument holder) — the ``_vptr`` name
    prefix is the identifying signal, not artificiality alone.
    """
    if child.tag != "DW_TAG_member" or not _attr_bool(child, "DW_AT_artificial"):
        return None
    member_name = _attr_str(child, "DW_AT_name") or ""
    if not member_name.startswith("_vptr"):
        return None
    loc = child.attributes.get("DW_AT_data_member_location")
    return _decode_member_location(loc.value if loc is not None else None) * 8


def variable_is_const(type_die: Any, CU: Any) -> bool:
    """Whether a variable's own OUTERMOST declared type is const-qualified,
    walking the LEADING run of pure cv-qualifier wrapper DIEs
    (``DW_TAG_const_type``/``DW_TAG_volatile_type``) rather than checking
    only the immediate one.

    ``const volatile int cv`` and ``volatile const int cv`` are the
    identical type in C++ (qualifier order in source is not semantically
    meaningful), but GCC does not always encode them with the type the
    variable's ``DW_AT_type`` points to being ``DW_TAG_const_type``
    itself -- confirmed with a real compiled fixture: ``const volatile
    int`` nests ``DW_TAG_volatile_type`` (outer) around
    ``DW_TAG_const_type`` (inner), so checking only the immediate type
    die's own tag (this function's own previous, inline form in
    ``dwarf_snapshot.py``) silently reported ``is_const=False`` for a
    genuinely const-qualified variable (Codex review, PR #1021, fresh
    evidence). Bounded to 10 wrapper levels -- the same defensive limit
    ``dwarf_snapshot._DwarfSnapshotBuilder._count_pointer_depth`` already
    uses for an analogous DIE-chain walk -- so a malformed or (in
    principle) cyclic DWARF chain can't loop forever; no real C++
    declaration nests cv-qualifiers anywhere near that deep.
    """
    depth = 0
    while type_die is not None and depth < 10:
        if type_die.tag == "DW_TAG_const_type":
            return True
        if type_die.tag != "DW_TAG_volatile_type":
            return False
        type_die = _resolve_type_die(type_die, CU)
        depth += 1
    return False


def access_from_dwarf(
    val: int, default: AccessLevel = AccessLevel.PUBLIC
) -> AccessLevel:
    """Map DW_AT_accessibility value to AccessLevel.

    *default* is returned for an absent attribute (``val == 0``) — the
    caller passes the enclosing record's actual language default (private
    for ``class``, public for ``struct``/``union``) where that distinction
    matters (record fields); other call sites keep the historical
    unconditional-public default.
    """
    if val == 2:  # DW_ACCESS_protected
        return AccessLevel.PROTECTED
    if val == 3:  # DW_ACCESS_private
        return AccessLevel.PRIVATE
    if val == 1:  # DW_ACCESS_public
        return AccessLevel.PUBLIC
    return default  # 0 (absent)
