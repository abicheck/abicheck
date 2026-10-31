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

"""Function removal when the ``functions`` detector is not comparable
(ADR-062 D14/D15).

The ``functions`` detector compares parameter and return types in the same
pass that pairs functions, so it reads the ``types`` section (and
``debug``, ``graph``, ``layout``, ``semantic_ir``); a stored snapshot whose
``types`` section is blocked or failed makes the whole detector not
comparable -- and a function that plainly vanished from the binary and the
declarations would go unreported.

``function_presence`` answers only the presence question, from the
sections presence needs: the declarations and the export table. It runs
only while ``functions`` is blocked, so the two never both report one
removal (its dedupe is by construction, not a post-pass). A removal it
reports is classified exactly as ``functions`` classifies it
(``diff_symbols._check_removed_function``): ``FUNC_REMOVED``, a visibility
change, an ELF-only removal or an inline removal. Pairing is by mangled
name only: the ``extern "C"`` by-name fallback and ctor/dtor key-drift
reconciliation compare signatures, which need the blocked sections. A
function whose mangled name changed is therefore reported removed here
(it is a different symbol to an old binary either way).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..detector_registry import registry
from .section_readers import DETECTOR_SECTIONS

if TYPE_CHECKING:
    from ..model import AbiSnapshot
    from ..model.change import Change

__all__ = ["FUNCTIONS_DETECTOR"]

#: The detector whose blocked state turns this one on.
FUNCTIONS_DETECTOR = "functions"


@registry.detector("function_presence")
def _diff_function_presence(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    reads = DETECTOR_SECTIONS[FUNCTIONS_DETECTOR]
    if not any(
        section in reads
        for snap in (old, new)
        for section, _diagnostic in snap.unreadable_sections
    ):
        return []  # `functions` ran and reported removals itself
    from ..compare.public_surface import public_functions
    from ..diff_symbols import _check_removed_function

    new_all = new.function_map
    new_public = public_functions(new)
    elf_only_mode = getattr(old, "elf_only_mode", False)
    changes: list[Change] = []
    for mangled, f_old in public_functions(old).items():
        if mangled in new_public:
            continue
        changes.append(_check_removed_function(mangled, f_old, new_all, elf_only_mode))
    return changes
