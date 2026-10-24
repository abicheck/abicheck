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

from enum import StrEnum


class ChangeEntity(StrEnum):
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


class ChangeOperation(StrEnum):
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


class ContractSubject(StrEnum):
    """What a finding's subject is, as far as a contract can place it (ADR-049
    amendment, "Contract subject of a ChangeKind").

    Only a ``DECLARATION`` names an entity a contract domain (headers, the
    export table, the Python API surface, a manifest) can commit to or
    exclude; contract evaluation answers ``NOT_APPLICABLE`` for every other
    value in every mode. Declared once per kind beside ``entity``, so a kind
    added tomorrow cannot fall through to ``UNKNOWN_UNRESOLVED`` and raise
    the contract-coverage exit the way hand-curated exemption lists let it.
    """

    #: A function, variable, type, enum, macro, Python object or source
    #: declaration of the library under test (or a roll-up of several).
    DECLARATION = "declaration"
    #: An edge from this library to another one: a ``DT_NEEDED`` entry, an
    #: imported symbol, a required version node, a resolved provider.
    DEPENDENCY_EDGE = "dependency_edge"
    #: A property of the whole artifact: format, architecture, loader state,
    #: hardening, the runtime or platform floor it requires.
    BINARY_WIDE = "binary_wide"
    #: A build, toolchain or package-configuration fact: a compiler flag or
    #: mode, a toolchain version, a build target, a release's composition.
    BUILD_FACT = "build_fact"
    #: A statement about the analysis or its evidence, not about the library.
    ANALYSIS = "analysis"
