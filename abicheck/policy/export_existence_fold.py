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

"""One finding per undeclared export that appeared or disappeared.

An export no public header declares that NEW gained is reported twice by two
independent producers: ``func_added_elf_only`` (the export set grew -- an
addition, which drives the MINOR bump) and ``exported_not_public`` stamped
``INTRODUCED`` (a new undocumented export). The same holds for a removal and
an ``exported_not_public`` stamped ``RESOLVED``. Both are true, but they are
one event about one symbol, and the report listed it twice (oneCCL: each of
ten real leaks).

The existence finding is the one kept: it carries the event and the addition
count, and ``workflows.export_existence_wording`` already words it from the
same accounting decision the hygiene finding was built from. The hygiene
finding moves to the redundant bucket with ``caused_by_type`` naming the
finding it folded into and the kept finding's ``caused_count`` incremented --
recorded, not discarded (``AGENTS.md``: record before disposing). A
redundant finding still reaches the verdict exactly as a kept one did
(``checker._verdict_scored_population``), so folding changes no verdict.

A hygiene finding is folded only into the existence finding of the matching
direction: ``INTRODUCED`` into an addition, ``RESOLVED`` into a removal, and
``NOT_EVALUATED`` (one side's evidence could not say) into either. A
``PERSISTENT`` one names an export present on both sides, which no existence
finding describes, and always stays.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..model.change_catalog.kinds import ChangeKind
from .evidence_status import CrossSourceEvolution

if TYPE_CHECKING:
    from ..checker_types import Change

__all__ = ["CAUSED_BY_PREFIX", "FoldExportHygieneIntoExistence", "fold_partner"]

#: ``Change.caused_by_type`` prefix a folded hygiene finding carries.
CAUSED_BY_PREFIX = "export_existence:"

_ADDED = frozenset({ChangeKind.FUNC_ADDED_ELF_ONLY, ChangeKind.VAR_ADDED_ELF_ONLY})
_REMOVED = frozenset(
    {ChangeKind.FUNC_REMOVED_ELF_ONLY, ChangeKind.VAR_REMOVED_ELF_ONLY}
)

#: Which existence direction each hygiene evolution state may fold into.
_DIRECTIONS: dict[CrossSourceEvolution, tuple[str, ...]] = {
    CrossSourceEvolution.INTRODUCED: ("added",),
    CrossSourceEvolution.RESOLVED: ("removed",),
    CrossSourceEvolution.NOT_EVALUATED: ("added", "removed"),
}


def fold_partner(
    hygiene: Change, existence: dict[tuple[str, str], Change]
) -> Change | None:
    """The existence finding *hygiene* folds into, or ``None`` to keep it."""
    if hygiene.kind != ChangeKind.EXPORTED_NOT_PUBLIC or not hygiene.symbol:
        return None
    state = getattr(hygiene, "cross_source_evolution", None)
    for direction in _DIRECTIONS.get(state, ()):  # type: ignore[arg-type]
        partner = existence.get((hygiene.symbol, direction))
        if partner is not None:
            return partner
    return None


class FoldExportHygieneIntoExistence:
    """Post-processing step: see the module docstring.

    Runs after suppression and rename pairing have settled which existence
    findings survive -- a suppressed or rename-paired existence finding is
    not in *changes* any more, so its hygiene twin is left standing rather
    than folded into a finding the report no longer shows.
    """

    name = "fold_export_hygiene_into_existence"

    def run(self, changes: list[Change], ctx: object) -> list[Change]:
        existence: dict[tuple[str, str], Change] = {}
        for c in changes:
            if c.kind in _ADDED and c.symbol:
                existence.setdefault((c.symbol, "added"), c)
            elif c.kind in _REMOVED and c.symbol:
                existence.setdefault((c.symbol, "removed"), c)
        if not existence:
            return changes
        kept: list[Change] = []
        redundant: list[Change] = ctx.redundant  # type: ignore[attr-defined]
        for c in changes:
            partner = fold_partner(c, existence)
            if partner is None:
                kept.append(c)
                continue
            c.caused_by_type = f"{CAUSED_BY_PREFIX}{partner.kind.value}:{c.symbol}"
            partner.caused_count += 1
            redundant.append(c)
        return kept
