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

"""Compatibility facade for the historical ``abicheck.bundle_facts`` import
path (G38 Phase 2, amendment to ADR-023).

ADR-061 gap E split this module's own implementation across three real
owners -- the split this repository's ``AGENTS.md`` already documents for
every ``*_metadata.py`` parser, applied here: the value type and its
construction invariant live in ``model.bundle_facts``; JSON/G40-archive
persistence lives in ``storage.bundle_facts_codec``/
``storage.bundle_facts_archive``; capture, live-snapshot reconstruction,
and comparison orchestration live in ``workflows.bundle_facts_capture``/
``workflows.bundle_facts_compare``. This module re-exports every one of
those names unchanged, so every existing ``from abicheck.bundle_facts
import ...`` call site -- including ``docs/use/multi-binary.md``'s
documented Python API and the test suite -- is unaffected. New internal
code imports the owning module directly (ADR-061 D6); this facade is for
external/back-compat callers only.
"""

from __future__ import annotations

from .model.bundle_facts import (
    BUNDLE_FACTS_ARTIFACT_TYPE,
    BUNDLE_FACTS_BASE_SCHEMA_VERSION,
    BUNDLE_FACTS_SCHEMA_VERSION,
    DEFAULT_VARIANT_FINGERPRINT,
    BundleFacts,
    document_schema_version,
    require_degraded_members_known,
)
from .storage.bundle_facts_archive import (
    BUNDLE_ARCHIVE_SCHEMA_VERSION,
    DEFAULT_MAX_BUNDLE_DECODED_BYTES,
    DEFAULT_MAX_JSON_OBJECT_NODES,
    DEFAULT_MAX_LIBRARY_COUNT,
    maybe_read_bundle_facts_archive,
    maybe_write_bundle_facts_archive,
    read_bundle_facts_archive,
    write_bundle_facts_archive,
)
from .storage.bundle_facts_validation import BUNDLE_ARCHIVE_ARTIFACT_TYPE
from .workflows.bundle_facts_capture import (
    bundle_snapshot_from_facts,
    capture_bundle_facts,
)
from .workflows.bundle_facts_compare import compare_bundle_from_facts

__all__ = [
    "BUNDLE_ARCHIVE_ARTIFACT_TYPE",
    "BUNDLE_ARCHIVE_SCHEMA_VERSION",
    "BUNDLE_FACTS_ARTIFACT_TYPE",
    "BUNDLE_FACTS_BASE_SCHEMA_VERSION",
    "BUNDLE_FACTS_SCHEMA_VERSION",
    "DEFAULT_MAX_BUNDLE_DECODED_BYTES",
    "DEFAULT_MAX_JSON_OBJECT_NODES",
    "DEFAULT_MAX_LIBRARY_COUNT",
    "DEFAULT_VARIANT_FINGERPRINT",
    "BundleFacts",
    "bundle_snapshot_from_facts",
    "capture_bundle_facts",
    "compare_bundle_from_facts",
    "document_schema_version",
    "maybe_read_bundle_facts_archive",
    "maybe_write_bundle_facts_archive",
    "read_bundle_facts_archive",
    "require_degraded_members_known",
    "write_bundle_facts_archive",
]
