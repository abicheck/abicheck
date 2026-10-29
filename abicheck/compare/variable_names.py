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

"""The reported spelling of a variable: its qualified name, not its leaf.

``Variable.name`` is the leaf identifier the header backends read off the
declaration (``K_V`` for ``struct K_S { static constexpr int K_V = 1; };``).
The scope (``K_S``, or a namespace) is carried by ``Variable.entity_id``. A
finding that reports the leaf alone cannot be matched against a finding that
reports the same variable by its qualified name (``K_S::K_V``, as
``compare.constants`` does), so every variable finding names the variable the
way the entity id spells it.

The entity id does not always carry the scope: a variable with a real Itanium
mangling is keyed by that mangling alone (``extra=("mangled", ...)``, no
scope segments), so for those the qualified spelling is the demangled symbol
(``_ZN3K_S3K_VE`` -> ``K_S::K_V``). A variable with neither a scope-bearing
entity id nor a C++ mangling (a C global, a DWARF-only declaration) falls back
to the leaf, the only spelling that producer has.
"""

from __future__ import annotations

from ..demangle import demangle
from ..model.identity import EntityId
from ..model.semantic_ir_legacy_adapter import render_display_name_or_leaf


def qualified_variable_name(
    entity_id: EntityId | None, leaf: str, *, mangled: str | None = None
) -> str:
    """The qualified spelling of a variable: its entity id's scope when it
    carries one, else the demangled *mangled* symbol, else *leaf*."""
    if entity_id is not None:
        rendered = render_display_name_or_leaf(entity_id)
        if rendered:
            return rendered
    if mangled:
        demangled = demangle(mangled)
        # A data symbol demangles without a parameter list; a function-shaped
        # spelling is not a variable's name, so it is not used.
        if demangled and "(" not in demangled:
            return demangled
    return leaf
