#!/usr/bin/env python3
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

"""Leaf types of the header-backend capability matrix.

The ``Capability`` status, the ``FactRow`` record and the derived hybrid column
are split out of ``backend_capabilities.py`` so its curated table can live in
``backend_capability_rows.py`` (the file-size gate caps the parent). Nothing
here depends on the parent.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from backend_capability_scan import COVERED_MODEL_CLASSES


class Capability(str, Enum):
    """What one backend can say about one fact."""

    #: The backend's own parse populates it.
    FULL = "full"
    #: Populated, but narrower/less precise than the sibling backend's value.
    #: The row's note says exactly how.
    PARTIAL = "partial"
    #: Populated only when the optional LibTooling companion tool
    #: (``ABICHECK_CLANG_LAYOUT_TOOL``, G28 Phase 4) is configured — the plain
    #: ``clang -ast-dump=json`` parse computes no record layout at all.
    COMPANION = "companion"
    #: Never populated by this backend; the field keeps its model default.
    NONE = "none"
    #: No header-AST backend populates it. Another layer owns the fact
    #: (DWARF, the ELF symbol table, or the post-parse provenance pass) — or,
    #: for a few fields, nothing does. The note says which.
    OTHER_LAYER = "other_layer"


#: Ranking used to derive the hybrid column. Only meaningful between the three
#: "this backend does populate it" values; the two absent states never win.
_RANK: dict[Capability, int] = {
    Capability.NONE: 0,
    Capability.COMPANION: 1,
    Capability.PARTIAL: 2,
    Capability.FULL: 3,
}


@dataclass(frozen=True)
class FactRow:
    """One model field's capability across the two header-AST backends."""

    owner: str  # one of COVERED_MODEL_CLASSES
    field: str  # the dataclass field name
    castxml: Capability
    clang: Capability
    #: True when ``workflows/dump/hybrid_merge.py`` explicitly backfills this field from the
    #: clang side of a merge. A hybrid snapshot is castxml-based, so a field
    #: NOT on that list keeps castxml's answer even when clang knows better —
    #: which is a real, documented consequence, not an oversight to paper over
    #: (see the generated "not inherited" note and the doc's Known gaps).
    hybrid_backfilled: bool = False
    note: str = ""

    def __post_init__(self) -> None:
        """Reject a row that contradicts itself before it can be published."""
        if self.owner not in COVERED_MODEL_CLASSES:
            raise ValueError(f"unknown owner {self.owner!r}")
        other = Capability.OTHER_LAYER
        if (self.castxml is other) != (self.clang is other):
            raise ValueError(
                f"{self.owner}.{self.field}: OTHER_LAYER describes a fact no "
                "header backend populates, so it must hold for both columns"
            )
        if self.castxml is other and self.hybrid_backfilled:
            raise ValueError(
                f"{self.owner}.{self.field}: a non-header fact cannot be "
                "backfilled by the hybrid merge"
            )
        if not self.note and (self.castxml is not self.clang):
            raise ValueError(
                f"{self.owner}.{self.field}: a row whose two backends differ "
                "must explain the difference in its note"
            )

    @property
    def populated_by_any_backend(self) -> bool:
        """Whether a header parse carries this fact at all.

        False means another layer owns it (DWARF, the symbol table, the
        provenance pass) or nothing does — such a row is a real field of the
        dataclass, but not a *header-backend* capability, so the summary
        counts exclude it.
        """
        return Capability.OTHER_LAYER not in (self.castxml, self.clang)


def hybrid_capability(row: FactRow) -> Capability:
    """The ``--ast-frontend hybrid`` column, derived from the other two.

    ``workflows.dump.hybrid_merge.merge_snapshots`` is castxml-based by construction: it
    walks castxml's declarations and takes clang's value only for the specific
    facts its backfill lists name (``_backfill_function_facts``,
    ``_merge_variable``, ``_merge_field``, ``_merge_record_type``'s
    ``_LAYOUT_SCALAR_ATTRS``, ``_merge_enum_type``). So:

    - a backfilled field resolves to whichever backend knows more, since
      castxml wins when it has a value and clang fills in when it doesn't;
    - every other field keeps **castxml's** answer, even one clang populates
      and castxml doesn't.

    The second rule is the one worth reading twice: hybrid is not the union of
    the two backends. A clang-only fact that nothing backfills reaches a hybrid
    snapshot **only** for a declaration clang alone saw (those are appended
    verbatim); for a declaration both backends saw — the common case — it is
    dropped.
    """
    if not row.populated_by_any_backend:
        return Capability.OTHER_LAYER
    if row.hybrid_backfilled:
        return max((row.castxml, row.clang), key=lambda c: _RANK[c])
    return row.castxml


# ---------------------------------------------------------------------------
# The curated matrix
# ---------------------------------------------------------------------------

_FULL = Capability.FULL
_PARTIAL = Capability.PARTIAL
_COMPANION = Capability.COMPANION
_NONE = Capability.NONE
_OTHER = Capability.OTHER_LAYER

_PROVENANCE_PASS = (
    "Set after parsing by `provenance.apply_provenance()` from the "
    "public-header set (`-H`/`--header`, plus `.abicheck.yml`'s "
    "`scope.public_header_dirs`), "
    "not by either backend."
)
_DYNSYM = "Read from the binary's own symbol table (`dumper_elf_symbols.py`)."
