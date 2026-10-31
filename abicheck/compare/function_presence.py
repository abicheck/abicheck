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
sections presence needs: the declarations, the export table and (for a
template instance) the header graph. It runs only while ``functions`` is
blocked, so the two never both report one removal (its dedupe is by
construction, not a post-pass). It classifies presence exactly as
``functions`` does on the same evidence -- pinned by a differential test
against that detector (``tests/test_function_presence_differential.py``):
it pairs on the same evidence-gap-reconciled surfaces
(``reconciled_function_surfaces``), reports a matched declaration's export
transition through ``export_transition.check_function``, defers an
unmatched template instance to ``TemplateInstanceAbsence``, and otherwise
classifies a removal with ``diff_symbols._check_removed_function``. When the
header graph is unreadable, an unmatched instance the new binary still
exports is declined, not reported removed. Pairing is by mangled name only:
the ``extern "C"`` by-name fallback and ctor/dtor key-drift reconciliation
compare signatures, which need the blocked sections. A function whose
mangled name changed is therefore reported removed here (it is a different
symbol to an old binary either way).
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


def _unreadable(snap: AbiSnapshot) -> frozenset[str]:
    return frozenset(section for section, _diagnostic in snap.unreadable_sections)


@registry.detector("function_presence")
def _diff_function_presence(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    reads = DETECTOR_SECTIONS[FUNCTIONS_DETECTOR]
    unreadable = _unreadable(old) | _unreadable(new)
    if not unreadable & reads:
        return []  # `functions` ran and reported removals itself
    from ..diff_symbols import _check_removed_function
    from .declined_comparisons import record_declined
    from .export_transition import check_function, deleted_declaration_is_public
    from .public_surface import reconciled_function_surfaces
    from .template_instance_absence import TemplateInstanceAbsence

    # The same evidence-gap-reconciled join `functions` pairs on, so a
    # declaration one side merely lacks public-contract evidence for is
    # matched rather than reported removed.
    old_map, new_map = reconciled_function_surfaces(old, new)
    new_all = new.function_map
    elf_only_mode = getattr(old, "elf_only_mode", False)
    template_absence = TemplateInstanceAbsence(new, old)
    # The template-instance rule reads the header graph; when that is
    # unreadable the rule cannot run, and an unmatched instance the new
    # binary still exports is left unresolved rather than reported removed.
    graph_readable = "graph" not in unreadable
    changes: list[Change] = []
    for mangled, f_old in old_map.items():
        f_new = new_map.get(mangled)
        if f_new is not None:
            # A matched declaration: only its export axis is presence.
            changes.extend(check_function(mangled, f_old, f_new))
            continue
        if deleted_declaration_is_public(new_all.get(mangled), f_old):
            record_declined(
                mangled,
                "declaration is deleted on the new side; the deletion finding "
                "needs sections the blocked `functions` detector reads",
            )
            continue
        if graph_readable:
            decided = template_absence.changes_for(mangled, f_old, elf_only_mode)
            if decided is not None:
                changes.extend(decided)
                continue
        elif template_absence.instance_still_exported(mangled):
            record_declined(
                mangled,
                "template instance absent from the new declarations but still "
                "exported; the header graph that decides it is unreadable",
            )
            continue
        changes.append(_check_removed_function(mangled, f_old, new_all, elf_only_mode))
    return changes
