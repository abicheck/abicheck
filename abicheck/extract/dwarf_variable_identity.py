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

"""A variable definition DIE read together with the declaration it completes.

An out-of-line definition of a C++ static data member (``alignas(16) int
A::x = 1;``) is a top-level ``DW_TAG_variable`` that carries only
``DW_AT_specification`` plus what the definition adds: clang writes the
linkage name and ``DW_AT_alignment`` there, while the name, ``DW_AT_external``
and type stay on the in-class declaration. Read alone, the definition has no
name and is dropped; and clang's in-class declaration has no linkage name, so
it does not match its export either. The variable was then missing from a
DWARF-only snapshot, with its declared alignment.

:func:`variable_definition_view` returns a view whose attributes are the
declaration's overlaid with the definition's, and the declaration's own
enclosing scope. It applies only when the declaration lives in the same unit
(a CU-relative reference): the declaration's type references are then valid
for the unit the caller resolves them against.
"""

from __future__ import annotations

from typing import Any

from ..dwarf_utils import attr_int, attr_str
from ..model.identity import ScopePath
from .dwarf_records import access_from_dwarf, default_member_access_for_tag
from .dwarf_scope import namespace_scope_segment, record_scope_segment

__all__ = ["variable_definition_view"]

_RECORD_TAGS = frozenset(
    {"DW_TAG_structure_type", "DW_TAG_class_type", "DW_TAG_union_type"}
)


class _DefinitionView:
    """A DIE stand-in: the definition's attributes over its declaration's."""

    __slots__ = ("_die", "attributes")

    def __init__(self, definition: Any, declaration: Any) -> None:
        self._die = definition
        self.attributes = {**declaration.attributes, **definition.attributes}
        # The definition is not a declaration, whatever the declaration says.
        self.attributes.pop("DW_AT_declaration", None)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._die, name)


def _enclosing_scope(declaration: Any) -> tuple[str, ScopePath] | None:
    """The ``A::B`` scope and its segments for *declaration*'s parents, or
    ``None`` when a parent cannot be named (an anonymous record)."""
    names: list[str] = []
    segments: list[Any] = []
    parent = declaration.get_parent()
    while parent is not None and parent.tag != "DW_TAG_compile_unit":
        name = attr_str(parent, "DW_AT_name")
        if parent.tag == "DW_TAG_namespace":
            if name:  # an anonymous namespace contributes no segment
                names.append(name)
                segments.append(namespace_scope_segment(parent, name))
        elif parent.tag in _RECORD_TAGS:
            if not name:
                return None
            grand = parent.get_parent()
            default = default_member_access_for_tag(
                grand.tag if grand is not None else ""
            )
            access = access_from_dwarf(attr_int(parent, "DW_AT_accessibility"), default)
            names.append(name)
            segments.append(record_scope_segment(name, access=access.value))
        else:
            return None
        parent = parent.get_parent()
    names.reverse()
    segments.reverse()
    return "::".join(names), tuple(segments)


def variable_definition_view(
    die: Any, CU: Any, scope: str, scope_path: ScopePath
) -> tuple[Any, str, ScopePath]:
    """``(die, scope, scope_path)`` for a variable DIE: unchanged, unless it
    is a nameless definition completing a same-unit declaration."""
    spec = die.attributes.get("DW_AT_specification")
    if spec is None or "DW_AT_name" in die.attributes:
        return die, scope, scope_path
    if spec.form == "DW_FORM_ref_addr":
        return die, scope, scope_path
    try:
        declaration = CU.get_DIE_from_refaddr(spec.value + CU.cu_offset)
        enclosing = _enclosing_scope(declaration)
    except Exception:  # noqa: BLE001 - an unreadable link leaves the DIE as is
        return die, scope, scope_path
    if enclosing is None:
        return die, scope, scope_path
    return _DefinitionView(die, declaration), enclosing[0], enclosing[1]
