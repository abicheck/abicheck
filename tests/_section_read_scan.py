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

"""AST oracle for `compare/section_readers.py`: the stored sections a
detector's code reads, derived from `_snapshot_read_scan.snapshot_reads` and
the storage layer's own section partition (`legacy_sections._SECTION_FIELDS`).
"""

from __future__ import annotations

from abicheck.storage.legacy_sections import _SECTION_FIELDS
from tests._snapshot_read_scan import snapshot_reads

#: ``AbiSnapshot`` attributes that are not stored fields themselves, mapped
#: to the section their value is derived from (``None``: runtime-only, or the
#: ``declarations`` container read, whose fields are listed separately).
DERIVED_ATTRS: dict[str, str | None] = {
    "declarations": None,
    "function_map": "declarations",
    "variable_map": "declarations",
    "canonical_ir": "semantic_ir",
    "stale_fact_families": "debug",
    "from_headers_inferred": "debug",
    "header_declaration_ids": None,
    "projection_unavailable": None,
    "projection_evidence_gaps": None,
    "unreadable_sections": None,
    "wire_extensions": None,
    "load_notices": None,
    "live_source_evidence": None,
    "live_preprocessor_clang_bin": None,
    "static_archive": None,
    "spellings_generation": "provenance",
    "declarations.debug_layout": "debug",
    "declarations.debug_advanced": "debug",
}


def section_of(attr: str) -> str | None:
    if attr in DERIVED_ATTRS:
        return DERIVED_ATTRS[attr]
    name = attr.removeprefix("declarations.")
    if name in ("semantic_ir", "semantic_ir_conflicts"):
        return "semantic_ir"
    for kind, fields in _SECTION_FIELDS.items():
        if name in fields:
            return kind
    raise KeyError(f"no stored section owns AbiSnapshot attribute {attr!r}")


def sections_read(fn: object) -> frozenset[str]:
    return frozenset(
        kind for attr in snapshot_reads(fn) if (kind := section_of(attr)) is not None
    )
