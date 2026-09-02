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

"""Snapshot storage-envelope operations a frontend asks the engine to perform.

ADR-061 Phase 4 item 2's "make workflows the sole operation owners" rule
applies to the ``storage`` ring the same way ``extraction.py`` applies it to
``extract``: a frontend may not import ``storage`` directly (``frontends.
may_import`` does not list it), so a CLI module that needs
``snapshot_io.py``'s compression/detection helpers reaches them through this
facade instead. Kept as its own module rather than folded into
``extraction.py``: these operations are ADR-059's storage-envelope
responsibility, not extraction, and mixing the two facades would blur exactly
the ownership boundary this ADR exists to keep explicit.

Re-export only, deliberately: the point is that there is one owner per
operation and the frontend reaches it through the workflow layer, not that a
new implementation appears here. Each name's own module (``snapshot_io.py``)
remains the one to read and to change.

``from ..x import y`` **binds** ``y`` here at import time, the same
consequence ``extraction.py``'s own docstring records -- a test that needs to
substitute one of these must patch it *here*, where the call actually
resolves.
"""

from __future__ import annotations

from ..project_snapshot_legacy import (
    is_project_snapshot_package_dir,
    read_legacy_snapshot_document,
    write_legacy_snapshot_package,
)
from ..snapshot_io import (
    _COMPRESSED_SUFFIXES,
    SnapshotCompression,
    bounded_decoded_prefix,
    detect_snapshot_compression,
    resolve_write_compression,
    write_snapshot_text,
)
from ..storage.sectioned_document import (
    from_sectioned_document,
    is_sectioned_document,
    to_sectioned_document,
)

__all__ = [
    "_COMPRESSED_SUFFIXES",
    "SnapshotCompression",
    "bounded_decoded_prefix",
    "detect_snapshot_compression",
    "from_sectioned_document",
    "is_project_snapshot_package_dir",
    "is_sectioned_document",
    "read_legacy_snapshot_document",
    "resolve_write_compression",
    "to_sectioned_document",
    "write_legacy_snapshot_package",
    "write_snapshot_text",
]
