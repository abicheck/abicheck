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


"""ADR-061 D9 taxonomy: symbol-level ChangeKind entries.

Function, variable, parameter, constant, and Python-API declaration facts --
the entities a linker/dynamic loader resolves by name, plus the C/C++ and
Python signature-level facts attached to them (linkage, inline-ness,
default arguments, access level, calling-convention-neutral qualifiers).
Distinguished from ``types.py`` (the type/layout side of the same
declarations) and from ``platform.py`` (the binary symbol-table
*representation* of the same names -- ELF/PE symbol binding, visibility,
and versioning, which are a platform-format concern rather than a
language-level one).

Categorized by which detector module actually produces each kind (verified
against the real ``ChangeKind.X`` construction sites in ``diff_symbols.py``
and its siblings -- ``diff_symbols_variables.py``, ``diff_symbols_renames.py``,
``diff_param_qualifiers.py``, ``diff_hidden_friends.py``,
``diff_python_api.py``, ``diff_python.py`` -- not by which flat
``change_registry_*.py`` sibling an entry happened to live in for pure
line-count reasons before this migration.
"""

from __future__ import annotations

from .registry import ChangeKindMeta
from .symbols_1 import SYMBOLS_ENTRIES_1
from .symbols_2 import SYMBOLS_ENTRIES_2

#: Assembled from the numbered parts above; split purely to keep each
#: file under ADR-061's 800-line ceiling, exactly as
#: ``kind_names_{1,2,3}.py`` already is. The public name is unchanged.
SYMBOLS_ENTRIES: list[ChangeKindMeta] = SYMBOLS_ENTRIES_1 + SYMBOLS_ENTRIES_2

__all__ = ["SYMBOLS_ENTRIES"]
