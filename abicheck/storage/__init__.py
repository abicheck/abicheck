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

"""Persistence layer — ADR-061's ``storage`` package, ADR-062's model.

This is a narrow *external* re-export surface, not an internal service
locator: code inside this package imports its siblings' implementation
modules directly (see ``AGENTS.md``).

ADR-062 Phase 0's availability/identity/canonical-encoding/versioning
primitives, plus Phase 1's ``package``/``dto``/``legacy_sections``/
``import_v1`` modules (the storage-format-v2 plan's A1.1-A1.3: the
``ProjectSnapshot`` manifest/ref/object-store model, its per-section DTO
envelope, the full D8 legacy-document section split, and the v1-v25 import
adapter/its exact inverse). ``sectioned_document`` (ADR-063 Phase 8,
redesigned) packages that same D8 split as one JSON document instead of a
directory -- the shape every real ``dump``/``compare``/``scan`` invocation
now reads and writes by default, via ``serialization.snapshot_to_json``/
``snapshot_from_dict`` (see that module's own docstring for why). The
directory-backed writer/reader (``abicheck.project_snapshot_store``,
``abicheck.project_snapshot_legacy``) still lives outside this package and
still works as a typed-API primitive, but no CLI flag writes one today.
ADR-059's physical envelope (compression, atomic writes, decompression-bomb
limits) stays in
``abicheck/snapshot_io.py`` and is deliberately not reimplemented here.
"""

from __future__ import annotations

from .availability import (
    AvailabilityLedger,
    Confidence,
    FactAvailability,
    FactStatus,
)
from .canonical import (
    CAPTURE_METADATA_KEY,
    canonical_form,
    canonical_json,
    raw_digest,
    semantic_digest,
    strip_capture_metadata,
)
from .dto import (
    GRAPH_SECTION_KIND,
    SECTION_SCHEMA_VERSIONS,
    SEMANTIC_IR_SECTION_KIND,
    TYPES_SECTION_KIND,
    SectionDTO,
    graph_from_dto,
    graph_to_dto,
    legacy_section_from_dto,
    legacy_section_to_dto,
    migrate_section_dto,
    semantic_ir_from_dto,
    semantic_ir_to_dto,
    types_from_dto,
    types_to_dto,
)
from .identity import (
    EntityId,
    EntityKind,
    IdentityConflict,
    ObservationKind,
    OccurrenceId,
    OccurrenceSet,
    elf_symbol_occurrence,
)
from .import_v1 import export_legacy_snapshot, import_legacy_snapshot
from .legacy_sections import (
    LEGACY_SECTION_KINDS,
    SCHEMA_VERSION_KEY,
    join_legacy_document,
    missing_required_section_fields,
    split_legacy_document,
)
from .package import (
    MANIFEST_RELPATH,
    SECTION_KINDS,
    ArtifactRef,
    InMemoryObjectStore,
    ObjectRef,
    ObjectStore,
    PackageManifest,
    VariantRef,
    artifact_ref_relpath,
    object_relpath,
    variant_ref_relpath,
)
from .sectioned_document import (
    SECTION_SCHEMA_VERSIONS_KEY,
    SECTIONS_KEY,
    from_sectioned_document,
    is_sectioned_document,
    to_sectioned_document,
)
from .versioning import (
    COMPARISON_CONTRACT_VERSION,
    PACKAGE_FORMAT_VERSION,
    UNSTATED_VERSION,
    ProducerIdentity,
    ReaderCompatibility,
    StorageVersions,
    check_reader_compatibility,
)

__all__ = [
    "ArtifactRef",
    "AvailabilityLedger",
    "CAPTURE_METADATA_KEY",
    "COMPARISON_CONTRACT_VERSION",
    "Confidence",
    "EntityId",
    "EntityKind",
    "FactAvailability",
    "FactStatus",
    "GRAPH_SECTION_KIND",
    "IdentityConflict",
    "InMemoryObjectStore",
    "LEGACY_SECTION_KINDS",
    "MANIFEST_RELPATH",
    "ObjectRef",
    "ObjectStore",
    "ObservationKind",
    "OccurrenceId",
    "OccurrenceSet",
    "PACKAGE_FORMAT_VERSION",
    "PackageManifest",
    "ProducerIdentity",
    "ReaderCompatibility",
    "SCHEMA_VERSION_KEY",
    "SECTIONS_KEY",
    "SECTION_KINDS",
    "SECTION_SCHEMA_VERSIONS",
    "SECTION_SCHEMA_VERSIONS_KEY",
    "SEMANTIC_IR_SECTION_KIND",
    "SectionDTO",
    "StorageVersions",
    "TYPES_SECTION_KIND",
    "UNSTATED_VERSION",
    "VariantRef",
    "artifact_ref_relpath",
    "canonical_form",
    "canonical_json",
    "check_reader_compatibility",
    "elf_symbol_occurrence",
    "export_legacy_snapshot",
    "from_sectioned_document",
    "graph_from_dto",
    "graph_to_dto",
    "import_legacy_snapshot",
    "is_sectioned_document",
    "join_legacy_document",
    "legacy_section_from_dto",
    "legacy_section_to_dto",
    "migrate_section_dto",
    "missing_required_section_fields",
    "object_relpath",
    "raw_digest",
    "semantic_digest",
    "semantic_ir_from_dto",
    "semantic_ir_to_dto",
    "split_legacy_document",
    "strip_capture_metadata",
    "to_sectioned_document",
    "types_from_dto",
    "types_to_dto",
    "variant_ref_relpath",
]
