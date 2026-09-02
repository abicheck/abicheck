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
primitives, plus Phase 1's ``package``/``dto``/``import_v1`` modules (the
storage-format-v2 plan's A1.1-A1.3: the ``ProjectSnapshot`` manifest/ref/
object-store model, its per-section DTO envelope, and the v1-v25 import
adapter). The real, directory-backed writer/reader
(``abicheck.project_snapshot_store``) lives outside this package, since it
needs ``snapshot_io`` and this package may import only ``model``. Nothing
here is wired into ``dump``/``compare``/``scan`` yet, so every existing
snapshot, baseline set, and ``BundleFacts`` document produced or read by
those commands is unchanged. ADR-059's physical envelope (compression,
atomic writes, decompression-bomb limits) stays in
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
    SECTION_SCHEMA_VERSIONS,
    SEMANTIC_IR_SECTION_KIND,
    SectionDTO,
    migrate_section_dto,
    semantic_ir_from_dto,
    semantic_ir_to_dto,
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
from .import_v1 import LEGACY_DOCUMENT_SECTION_KIND, import_legacy_snapshot
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
    "IdentityConflict",
    "InMemoryObjectStore",
    "LEGACY_DOCUMENT_SECTION_KIND",
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
    "SECTION_KINDS",
    "SECTION_SCHEMA_VERSIONS",
    "SEMANTIC_IR_SECTION_KIND",
    "SectionDTO",
    "StorageVersions",
    "UNSTATED_VERSION",
    "VariantRef",
    "artifact_ref_relpath",
    "canonical_form",
    "canonical_json",
    "check_reader_compatibility",
    "elf_symbol_occurrence",
    "import_legacy_snapshot",
    "migrate_section_dto",
    "object_relpath",
    "raw_digest",
    "semantic_digest",
    "semantic_ir_from_dto",
    "semantic_ir_to_dto",
    "strip_capture_metadata",
    "variant_ref_relpath",
]
