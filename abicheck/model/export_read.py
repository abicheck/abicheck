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

"""How completely a platform block's export table was read (ADR-079).

``parse_{elf,pe,macho}_metadata`` keep going past a malformed section and
return what they read. :class:`ExportReadState` records the outcome of that
read on the platform block itself (``ElfMetadata``/``PeMetadata``/
``MachoMetadata.export_read``), so a stored snapshot can say its table was
cut short. The document key is ``export_read`` on each platform block
(snapshot schema v70); see ``storage/snapshot_schema_versions.py``.

A leaf: no imports from the rest of ``model``, so each platform-facts module
can carry the field without an import cycle.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["ExportReadState", "export_read_state"]


class ExportReadState(StrEnum):
    """How the export table of one platform block was read (ADR-079 D1/D3)."""

    #: Every export-table section the parser reached was read.
    COMPLETE = "complete"
    #: At least one export was read and at least one export-table read
    #: failure was recorded: the entries are real, the unread part is unknown,
    #: never absent.
    PARTIAL = "partial"
    #: A read failure was recorded and no export was read.
    FAILED = "failed"
    #: No record of the read: a document written before schema v70, or a
    #: block no recording read produced (a hand-built object). The readers
    #: keep the pre-v70 rule for it (``model.export_index.
    #: platform_block_parsed``) -- never read as ``complete``.
    UNRECORDED = "unrecorded"


def export_read_state(*, failure_noted: bool, exports_read: bool) -> ExportReadState:
    """ADR-079 D1: the state a recording read reports.

    *failure_noted* is whether the read recorded at least one export-table
    failure (``extract/parse_failures.py``); *exports_read* whether it read
    at least one export.
    """
    if not failure_noted:
        return ExportReadState.COMPLETE
    return ExportReadState.PARTIAL if exports_read else ExportReadState.FAILED
