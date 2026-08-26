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

ADR-062 Phase 0 primitives only. Nothing here is wired into a producer,
reader, or comparison path yet, so every existing snapshot, baseline set,
and ``BundleFacts`` document is unchanged. ADR-059's physical envelope
(compression, atomic writes, decompression-bomb limits) stays in
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
    VOLATILE_KEYS,
    canonical_form,
    canonical_json,
    semantic_digest,
    strip_volatile,
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
from .versioning import (
    COMPARISON_CONTRACT_VERSION,
    PACKAGE_FORMAT_VERSION,
    ProducerIdentity,
    ReaderCompatibility,
    StorageVersions,
    check_reader_compatibility,
)

__all__ = [
    "COMPARISON_CONTRACT_VERSION",
    "PACKAGE_FORMAT_VERSION",
    "VOLATILE_KEYS",
    "AvailabilityLedger",
    "Confidence",
    "EntityId",
    "EntityKind",
    "FactAvailability",
    "FactStatus",
    "IdentityConflict",
    "ObservationKind",
    "OccurrenceId",
    "OccurrenceSet",
    "ProducerIdentity",
    "ReaderCompatibility",
    "StorageVersions",
    "canonical_form",
    "canonical_json",
    "check_reader_compatibility",
    "elf_symbol_occurrence",
    "semantic_digest",
    "strip_volatile",
]
