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


"""ADR-061 D9 taxonomy: type/layout-level ChangeKind entries.

Struct/class/union/enum/typedef declarations and everything about their
shape: fields, bases, vtables, layout (size/alignment/offset), kind
(struct vs. class vs. union), template parameters, and C++-specific
member-function qualifiers that are properties of the type's own
declaration (``const``/``ref``-qualified, ``static``, pure-virtual) rather
than of a free function's own linkage.

Categorized by which detector module actually produces each kind (verified
against the real ``ChangeKind.X`` construction sites in ``diff_types.py``
and its siblings -- ``diff_types_abicc_parity.py``,
``diff_types_field_facts.py``, ``diff_layout.py``, ``diff_elf_layout.py``,
``diff_vtable_layout.py``, ``diff_namespaces.py``, ``diff_stdlib_impl.py``,
``diff_cpp_patterns.py``, ``diff_templates.py``,
``diff_platform_templates.py`` -- not by which flat
``change_registry_*.py`` sibling an entry happened to live in for pure
line-count reasons before this migration.
"""

from __future__ import annotations

from .registry import ChangeKindMeta
from .types_1 import TYPES_ENTRIES_1
from .types_2 import TYPES_ENTRIES_2

#: Assembled from the numbered parts above; split purely to keep each
#: file under ADR-061's 800-line ceiling, exactly as
#: ``kind_names_{1,2,3}.py`` already is. The public name is unchanged.
TYPES_ENTRIES: list[ChangeKindMeta] = TYPES_ENTRIES_1 + TYPES_ENTRIES_2

__all__ = ["TYPES_ENTRIES"]
