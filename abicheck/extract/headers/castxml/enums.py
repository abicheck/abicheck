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

"""Enum-entity parsing for the castxml backend (ADR-061 D9).

First entity module split out of ``_CastxmlParser`` proper (``names.py``
moved pure helpers, not entity parsing). Reads ``ctx.enum_els`` — populated
once by :meth:`~.context.CastxmlParserContext.build_id_map` — and produces
``EnumType`` model objects, using the shared location/type-resolution
modules in this package for everything below the entity level. Creates no
policy finding and resolves nothing global; ``dumper_castxml.py`` still
owns opening the castxml document and driving ``build_id_map()``.
"""

from __future__ import annotations

from ....model import EnumMember, EnumType, Fact
from ....model.identity import entity_id_for_enum
from ....name_classification import strip_anonymous_type_location
from .context import CastxmlParserContext
from .location import (
    deprecation_marker as _deprecation_marker,
    is_builtin_element,
    source_location,
)
from .scope import scope_path
from .type_resolution import (
    qualified_type_name,
    underlying_type_name as _underlying_type_name,
)


def parse_enums(ctx: CastxmlParserContext) -> list[EnumType]:
    enums = []
    for el in ctx.enum_els:
        name = strip_anonymous_type_location(el.get("name", ""))
        if not name or name.startswith("__"):
            continue
        if is_builtin_element(ctx, el):
            continue
        members = []
        for child in el:
            if child.tag == "EnumValue":
                m_name = child.get("name", "")
                m_val_str = child.get("init", "0")
                try:
                    # base=0 auto-detects 0x.../0o.../0b... prefixes and signs
                    # so common C/C++ initializers like 0x10 don't silently
                    # collapse to 0.
                    m_val = int(m_val_str, 0)
                except ValueError:
                    m_val = 0
                members.append(EnumMember(name=m_name, value=m_val))
        enum_type_id = el.get("type", "")
        underlying_type = (
            _underlying_type_name(ctx, enum_type_id) if enum_type_id else "int"
        )
        # ADR-063 Phase 5 (third batch): captured for the explicit
        # qualified_name_fact=Fact.present(...) construction below -- see
        # RecordType's identical castxml construction site for why a None
        # return here is treated as a confirmed determination, not omitted
        # evidence (the qualified_type_name() cycle/depth-cap caveat is a
        # pathological, essentially unobserved edge case).
        qualified_name = qualified_type_name(ctx, el, leaf_name=name)
        enums.append(
            EnumType(
                name=name,
                members=members,
                underlying_type=underlying_type,
                source_location=source_location(ctx, el),
                # castxml's `scoped="1"` marks `enum class`/`enum struct`.
                is_scoped=el.get("scoped") == "1",
                # See RecordType.deprecated for the message-text convention.
                deprecated=_deprecation_marker(el),
                # See RecordType.qualified_name for the bare-vs-qualified
                # name convention this mirrors.
                qualified_name=qualified_name,
                qualified_name_fact=Fact.present(qualified_name),
                # ADR-063 Phase 2 -- see build_record_type's own comment.
                entity_id=entity_id_for_enum(scope_path(ctx, el), name),
            )
        )
    return enums
