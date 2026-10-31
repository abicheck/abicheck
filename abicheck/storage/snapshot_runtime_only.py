# Copyright 2026 Nikolay Petrov
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

"""Runtime-only ``AbiSnapshot`` fields: the single declaration the encoder
drops and the field round-trip test reads.

A field listed here is set by one run (an extraction, a load, a depth
projection) and is never a property the stored document carries. The encoder
(``storage/snapshot_encode.py``) omits exactly these keys; the round-trip test
(``tests/unit/storage/test_snapshot_field_roundtrip.py``) enumerates the rest
of the model and reads this mapping rather than keeping its own copy.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

__all__ = ["RUNTIME_ONLY_SNAPSHOT_FIELDS"]

#: The ``AbiSnapshot`` fields the encoder deliberately never persists, each
#: with the reason it is runtime-only. This is the single declaration: the
#: encoder drops exactly these keys, and the field round-trip test
#: (``tests/unit/storage/test_snapshot_field_roundtrip.py``) reads it rather
#: than keeping a copy. A field listed here must be runtime-only for one run
#: alone; a fact a stored snapshot needs to reproduce its own result belongs in
#: the document instead.
RUNTIME_ONLY_SNAPSHOT_FIELDS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "from_headers_inferred": (
            "a provenance guess recomputed on load from the surface; the key "
            "is dropped with it so a reload re-infers the same answer"
        ),
        "live_source_evidence": (
            "a source-read licence this run's extraction granted; a stored "
            "snapshot must not license itself (buildsource/source_inputs.py)"
        ),
        "live_preprocessor_clang_bin": (
            "the preprocessor this run's L4 replay used; a stored snapshot "
            "falls back to the pre-scan's own default"
        ),
        "load_notices": (
            "notices about this run's load (a generation drift), not a property "
            "the snapshot carries"
        ),
        "static_archive": (
            "whether this run read the export table from an archive's own symbol index"
        ),
        "header_declaration_ids": (
            "the L2 node ids a depth projection kept for this run; ids only, so "
            "they can never read as L5 evidence"
        ),
        "projection_unavailable": (
            "the detectors a depth projection disabled for this run"
        ),
        "unreadable_sections": (
            "the stored sections this run's load could not use; the stored "
            "document keeps them verbatim"
        ),
        "wire_extensions": (
            "the stored document's D14/D15 carriers this run's load kept; the "
            "sectioned writer merges them back into the document it writes"
        ),
        "projection_evidence_gaps": (
            "the evidence a depth projection could not reproduce for this run"
        ),
    }
)
