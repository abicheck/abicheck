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

"""Additions observable only in the export table (``func_added_elf_only``).

A sibling of :mod:`abicheck.diff_symbols` rather than a function inside it:
that module is at the AI-readiness file-size cap, and ``AGENTS.md``'s rule
for a file at the cap is to move responsibility out to an owned module, not
to trim the file to fit. This detector owns one question -- did the export
set grow by a symbol no public header declares -- and reads only the two
sides' ELF export tables to answer it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..detector_registry import registry
from ..diff_helpers import make_change
from ..diff_symbols_renames import _should_filter_transitive_runtime_symbols
from ..elf_symbol_filter import FUNCTION_SYMBOL_TYPES, exported_symbol_names
from ..model.change_catalog.kinds import ChangeKind

if TYPE_CHECKING:
    from ..checker_types import Change
    from ..model import AbiSnapshot


@registry.detector("undeclared_exports")
def _diff_undeclared_exports(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Exports that appeared in NEW and are declared in no public header.

    A header-aware snapshot builds ``function_map`` from the header AST, so
    an exported symbol that no header declares never enters it -- and the
    old/new function diff, which reads that map, cannot see such a symbol
    appear. The same release reported ``Additions (1)`` at ``--depth binary``
    and ``Additions (0)`` with ``-H``, and the export was reachable only as
    an ``exported_not_public`` *hygiene* finding, which is a different claim
    (whether the export should be undeclared) and drives neither the addition
    count nor ``semver.recommend_release``'s MINOR bump.

    Deliberately **not** implemented by synthesizing export-only records into
    ``function_map``, which was the other candidate. That map is read by every
    detector in this package, so widening it in header-aware mode -- the
    common case -- changes what all of them see, for one missing addition.
    This reads the two export tables directly instead and emits nothing else.

    The removal direction is deliberately absent, and that asymmetry is the
    point rather than an omission: an export disappearing is a potential
    binary break and asserting one from export-table evidence alone, on a
    symbol the headers never promised, is exactly the kind of unproven
    finding ``vision.md`` forbids -- ``exported_not_public``'s disappearance
    already shows up as that check's ``RESOLVED`` state. An *addition* claims
    only that the export set grew, which the two tables do prove.
    """
    old_elf = getattr(old, "elf", None)
    new_elf = getattr(new, "elf", None)
    if old_elf is None or new_elf is None:
        return []
    if not getattr(old_elf, "symbols", None) or not getattr(new_elf, "symbols", None):
        # One side's export table was never captured. "Not observed" is not
        # "empty": treating it as empty would report every export in the
        # other side as newly added (ADR-028/049 -- missing evidence never
        # fabricates a finding).
        return []
    # A headerless NEW already reports these through the ordinary function
    # diff, because `dumper_elf_fallback` puts export-only records in
    # `function_map` for exactly that case. Emitting here too would
    # double-report every added symbol in every binary-depth run.
    #
    # NEW's mode is what decides that, and OLD's is deliberately not
    # consulted. An *addition* is a symbol present in NEW and absent from
    # OLD, so the ordinary diff can only name it if it reached NEW's
    # `function_map` -- which the fallback does only for a headerless NEW.
    # In the mixed shape (ELF-only OLD, header-aware NEW) an undeclared new
    # export is in neither map, so keying the guard off OLD as well would
    # suppress the one detector that can see it and lose the addition
    # entirely. There is no double-report risk from that shape, because the
    # ordinary diff never reported it in the first place.
    if getattr(new, "elf_only_mode", False):
        return []

    filter_transitive = _should_filter_transitive_runtime_symbols(new)
    old_exports = exported_symbol_names(
        old_elf,
        FUNCTION_SYMBOL_TYPES,
        abi_relevant_only=True,
        filter_transitive_runtime_symbols=filter_transitive,
    )
    new_exports = exported_symbol_names(
        new_elf,
        FUNCTION_SYMBOL_TYPES,
        abi_relevant_only=True,
        filter_transitive_runtime_symbols=filter_transitive,
    )
    gained = new_exports - old_exports
    if not gained:
        return []

    # Anything either side declares is the ordinary function diff's business:
    # it is in `function_map` and `FUNC_ADDED` (or a virtual-addition break)
    # already covers it. Both maps are consulted, not just the new one, so a
    # symbol that was declared in OLD's headers and dropped from NEW's cannot
    # arrive here as an "addition" -- that is a declaration change, not a new
    # export.
    declared = set(old.function_map) | set(new.function_map)
    declared_names = {f.name for f in old.function_map.values()} | {
        f.name for f in new.function_map.values()
    }

    changes: list[Change] = []
    for mangled in sorted(gained):
        if mangled in declared or mangled in declared_names:
            continue
        f_new = new.function_map.get(mangled)
        changes.append(
            make_change(
                ChangeKind.FUNC_ADDED_ELF_ONLY,
                symbol=mangled,
                # `name` is what the kind's description_template renders; the
                # export table carries only the mangled spelling, so that is
                # the honest value for both. `new` additionally populates
                # `Change.new_value`, which the addition renderers read.
                name=mangled,
                new=mangled,
                entity_id=f_new.entity_id if f_new is not None else None,
            )
        )
    return changes
