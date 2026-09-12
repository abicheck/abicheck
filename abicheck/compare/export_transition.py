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

"""The *binary* half of a surface change whose declaration did not change.

One detector, owned here rather than grown into ``diff_symbols.py``: it
belongs to the export axis, not the signature axis every other check in
that module compares. See ``model/surface_facts.py`` for the three facts it
reads and the ``Visibility.PUBLIC`` entry in
``docs/contribute/known-gaps.md`` for why this finding exists at all.
"""

from __future__ import annotations

from ..checker_types import Change
from ..diff_helpers import make_change
from ..model import Function
from ..model.change_catalog.kinds import ChangeKind
from ..model.surface_facts import (
    is_binary_exported,
    is_export_confirmed_absent,
    surface_fact_summary,
)

__all__ = ["check_export_lost"]


def check_export_lost(mangled: str, f_old: Function, f_new: Function) -> list[Change]:
    """A matched pair whose export went away while its declaration stayed.

    This is the second of the two findings the ``Visibility.PUBLIC``
    known-gaps entry calls for. Before the split, a declaration that stayed
    byte-identical in the headers while the artifact stopped exporting it
    left the new side out of the public index entirely, and the run reported
    one wrong finding -- a removed source API. Now the declaration keeps its
    place (``in_source_declaration_index``) and the export change is
    reported on its own axis instead: a disappearing dynamic export can
    still break an already-linked consumer, so it is not something to
    ignore, it is something to report as what it is.

    Gated on *confirmed* evidence on both sides (``PRESENT``/``PARTIAL``
    with a real value): "exported before, unknown now" is a gap in this
    run's evidence, not an observed transition, and must not be rendered as
    one. Findings whose old side was never exported, or whose new side still
    is, produce nothing here.
    """
    if not (is_binary_exported(f_old) and is_export_confirmed_absent(f_new)):
        return []
    # A removal reported elsewhere is not this: the caller only reaches here
    # for a *matched* pair, i.e. a declaration present on both sides.
    return [
        make_change(
            ChangeKind.FUNC_VISIBILITY_CHANGED,
            symbol=mangled,
            name=f_old.name,
            description=(
                f"Function no longer exported by the binary, but still "
                f"declared in the available headers: {f_old.name}"
            ),
            old_value=f_old.visibility.value,
            new_value=f_new.visibility.value,
            symbol_binding=f_old.elf_binding.value if f_old.elf_binding else None,
            entity_id=f_old.entity_id or f_new.entity_id,
            surface_facts=surface_fact_summary(f_new),
        )
    ]
