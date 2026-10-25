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

"""Evidence a ``binary`` projection of a header-built snapshot cannot rebuild.

A binary-only dump reads function/variable signatures, typedefs and record
relations (bases, vtable slots, member access, field qualifiers) from the
debug-info records. A header-built snapshot keeps the header's spelling of
those facts instead and does not persist the debug-info records, so its
``--depth binary`` projection (``policy/depth_projection.py``) drops the
header spelling and has nothing to put back. The projection still compares
the export table and the persisted debug layouts, but a signature, typedef,
vtable or base change goes unseen.

That is missing evidence, not a clean result. The projection records each
gap on the projected snapshot (``AbiSnapshot.projection_evidence_gaps``), and
``policy/analysis_assurance/_depth_projection.py`` lowers the run's assurance
to ``partial`` and names them (bug class
``evidence.depth_projection_diverges_from_shallow_dump``; known gap
"``--depth binary`` on a header-built snapshot misses debug-only declaration
findings").
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..model import AbiSnapshot

__all__ = ["DEBUG_DECLARATION_GAPS", "binary_projection_gaps"]

#: The evidence names, in report order.
DEBUG_DECLARATION_GAPS: tuple[str, ...] = (
    "debug-info function/variable signatures and typedefs "
    "(not persisted in a header-built snapshot)",
    "debug-info record relations: bases, vtable slots, member access, field "
    "qualifiers (not persisted in a header-built snapshot)",
)


def binary_projection_gaps(snap: AbiSnapshot, dwarf_sourced: bool) -> tuple[str, ...]:
    """The evidence *snap*'s ``binary`` projection loses and cannot rebuild.

    Empty when the kept structural facts are the debug-info ones
    (*dwarf_sourced*), or when *snap* was not header-built or carries no debug
    info: then a binary-only dump of the same library has no signature
    evidence either, so nothing is missed.
    """
    from ..model.debug_evidence import debug_info_evidence

    if dwarf_sourced or not snap.from_headers:
        return ()
    if not debug_info_evidence(snap).basic:
        return ()
    return DEBUG_DECLARATION_GAPS
