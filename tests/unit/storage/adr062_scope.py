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

"""Which modules in `abicheck/storage/` the ADR-062 sweeps police.

`abicheck/storage/` holds two independent bodies of work: G40's
bundle-archive container and ADR-062's Phase 0 primitives. The sweeps in
this directory state ADR-062's invariants — a lookup key is validated, a
`from_dict` guards its container, a required field is present — and those
are claims about the Phase 0 primitives, not about G40's byte-level
container, which has its own contract in the package's `AGENTS.md`.

The set is defined by **exclusion** rather than by listing the ADR-062
modules, and that direction is the point: a module added to this package is
swept by default, and escapes only when someone states which body of work it
belongs to. Listing the included set instead would mean a new Phase 0 module
silently escapes every sweep here — which is the exact failure this
directory's tests exist to prevent, several times over.

A new G40 module therefore fails these sweeps until it is named below. That
is a loud, one-line resolution, and it errs in the direction that costs a
false alarm rather than a missed defect.
"""

from __future__ import annotations

import pathlib

#: Modules that are not ADR-062 Phase 0 primitives. G40's content-addressed
#: bundle archive and its guards, per `abicheck/storage/AGENTS.md`, plus
#: ADR-063's own snapshot encode/decode/legacy-backfill helpers (Phase 0's
#: Fact[T] pair `fact_codec`/`enum_codec`, Phase 5's `fact_backfill` and the
#: `fact_schema_versions` leaf they share, Phase 2's `entity_id_codec`,
#: which keeps the parse-time `EntityId` carrier out of the wire format, and
#: Phase 3's `surface_graph_codec` and Phase 6's `semantic_ir_codec`, the
#: identical shape for `AbiSnapshot.surface_graph`/`semantic_ir`, plus
#: Track 4/8B's own `types_section_codec`/`graph_section_codec`/
#: `sparse_section_codec` typed per-legacy-section DTOs) — a third,
#: independent body of work in this
#: package, unrelated to either ADR-062's Phase 0 primitives or G40's
#: container. `snapshot_load_normalization` is a fourth, equally unrelated
#: body of work: `serialization.snapshot_from_dict`'s on-load legacy-format
#: migrations (ADR-061 D1's storage-owns-migrations rule), not a Phase 0
#: identity/availability/versioning primitive.
NON_ADR062_MODULES = frozenset(
    {
        "bundle_archive",
        "bundle_archive_cd_guard",
        "bundle_archive_json_guard",
        "bundle_facts_validation",
        "json_budget",
        "zstd_frame_guard",
        "fact_codec",
        "fact_backfill",
        "fact_schema_versions",
        "enum_codec",
        "entity_id_codec",
        "surface_graph_codec",
        "semantic_ir_codec",
        "types_section_codec",
        "graph_section_codec",
        "sparse_section_codec",
        "snapshot_load_normalization",
    }
)

STORAGE_PACKAGE = pathlib.Path("abicheck/storage")


def adr062_module_paths() -> list[pathlib.Path]:
    """Every ADR-062 Phase 0 module, `__init__` excluded."""
    return sorted(
        path
        for path in STORAGE_PACKAGE.glob("*.py")
        if path.stem != "__init__" and path.stem not in NON_ADR062_MODULES
    )
