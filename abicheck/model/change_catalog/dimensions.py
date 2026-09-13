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

"""The two canonical display dimensions of a ``ChangeKind`` (plan slice 7o).

A leaf of the leaf: ``registry.py`` is already at the ADR-061 new-file line
ceiling, and these two enums are a self-contained vocabulary that
``ChangeKindMeta`` merely *carries* -- the same mechanical extraction
``snapshot_schema_versions.py`` and ``adr_status_sync.py`` made for their
own callers. Zero internal imports, like the module that re-exports them, so
every existing ``from ...registry import ChangeEntity`` call site keeps
working.
"""

from __future__ import annotations

from enum import Enum


class ChangeEntity(str, Enum):
    """Which kind of thing a finding is *about* -- the canonical display
    "element" dimension (plan slice 7o).

    Declared once per ``ChangeKind`` in the taxonomy modules, beside its
    verdict and impact text, so the reporter's display filter reads a fact
    the catalog states rather than re-deriving one from the kind's *name*.
    The superseded derivation lived in ``reporter_markdown`` as a table of
    name prefixes plus an exact-match escape list, and it silently mapped
    238 of 407 kinds to *no* element at all -- so ``--view
    show=functions,variables,types,enums,elf`` (every token the vocabulary
    had) hid 58% of the catalog. A kind added tomorrow cannot repeat that:
    the field is mandatory on the single registration.
    """

    FUNCTION = "function"
    VARIABLE = "variable"
    TYPE = "type"
    ENUM = "enum"
    BINARY = "binary"
    BUILD = "build"
    SOURCE = "source"
    ANALYSIS = "analysis"


class ChangeOperation(str, Enum):
    """Whether a finding reports an entity appearing, disappearing, or
    changing in place -- the canonical display "action" dimension.

    Superseded the same way ``ChangeEntity`` did: a suffix rule over the
    kind's name (``*_added``/``*_removed``/everything else) plus a
    hand-maintained 30-entry override table for every kind whose name ends
    in ``_added`` while naming a *trait gained by a persisting entity*
    (``func_noexcept_added``, ``type_field_added``, ...). The override table
    was the evidence that the name is not the fact; the fact now lives in
    the catalog.
    """

    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"
