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
from ..elf_symbol_filter import (
    FUNCTION_SYMBOL_TYPES,
    VARIABLE_SYMBOL_TYPES,
    exported_symbol_names,
)
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
    # "Not observed" is not "empty", and the two have to be told apart from
    # something other than the symbol list, which is a plain `list` that a
    # default/parse-failed `ElfMetadata()` and a real zero-export library
    # both leave empty. `machine` is the repo's existing capture signal: a
    # real parsed ELF always sets it, a default/header-only/parse-failed one
    # has `""` (see `diff_platform_elf_dynamic`'s identical guard).
    #
    # Keying on emptiness instead -- the first version -- suppressed the
    # detector for a library that genuinely exports nothing, so the first
    # undeclared export it ever gained went unreported: the ordinary function
    # diff cannot see an undeclared symbol either, so nothing reported it at
    # all (Codex review). The asymmetry matters and is why this guard stays
    # strict rather than being dropped: an *uncaptured* OLD table would make
    # every export in NEW read as gained, which is the fabricated finding
    # ADR-028/049 forbids.
    if not getattr(old_elf, "machine", "") or not getattr(new_elf, "machine", ""):
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

    # Every ABI-relevant export name OLD carried, across *both* symbol
    # classes, computed once. A name present here is not an addition however
    # its type changed, which the per-class subtraction below cannot see on
    # its own: an export that keeps its name and changes ELF type
    # (`STT_OBJECT` -> `STT_FUNC`) is absent from the function-only
    # `old_exports`, so it read as newly added and `func_added_elf_only` was
    # emitted alongside the `symbol_type_changed` that already describes the
    # real modification -- corrupting the addition count and, through it,
    # `-warn-newsym` and `semver.recommend_release`'s MINOR bump (Codex
    # review; reproduced on real binaries with a variable turned into a
    # function of the same name).
    old_export_names: set[str] = set()
    for types in (FUNCTION_SYMBOL_TYPES, VARIABLE_SYMBOL_TYPES):
        old_export_names |= exported_symbol_names(
            old_elf,
            types,
            abi_relevant_only=True,
            filter_transitive_runtime_symbols=filter_transitive,
        )

    changes: list[Change] = []
    # Functions and data symbols, the same way. Splitting the loop was the
    # first shape and it lost the data half outright: an undeclared
    # STT_OBJECT/STT_TLS/STT_COMMON export is absent from the header-derived
    # `variable_map` exactly as an undeclared function is from
    # `function_map`, so `_diff_variables` could not report VAR_ADDED for it
    # either and the addition disappeared entirely (Codex review). One loop
    # over both classes is what keeps the next symbol class from being
    # forgotten the same way.
    for symbol_types, declaring_maps, kind in (
        (
            FUNCTION_SYMBOL_TYPES,
            (old.function_map, new.function_map),
            ChangeKind.FUNC_ADDED_ELF_ONLY,
        ),
        (
            VARIABLE_SYMBOL_TYPES,
            (old.variable_map, new.variable_map),
            ChangeKind.VAR_ADDED_ELF_ONLY,
        ),
    ):
        old_exports = exported_symbol_names(
            old_elf,
            symbol_types,
            abi_relevant_only=True,
            filter_transitive_runtime_symbols=filter_transitive,
        )
        new_exports = exported_symbol_names(
            new_elf,
            symbol_types,
            abi_relevant_only=True,
            filter_transitive_runtime_symbols=filter_transitive,
        )
        # `old_export_names`, not just this class's `old_exports`: see above.
        # NEW's own type still decides *which* kind a genuinely new name gets,
        # which is why the per-class `new_exports` stays as it is.
        gained = new_exports - old_exports - old_export_names
        if not gained:
            continue

        # Anything either side declares is the ordinary diff's business: it
        # is in the matching map and FUNC_ADDED/VAR_ADDED already covers it.
        # Both sides' maps are consulted, not just the new one, so a symbol
        # declared in OLD's headers and dropped from NEW's cannot arrive here
        # as an "addition" -- that is a declaration change, not a new export.
        old_map, new_map = declaring_maps
        declared = set(old_map) | set(new_map)
        declared_names = {d.name for d in old_map.values()} | {
            d.name for d in new_map.values()
        }
        # `notype` is in *both* symbol-type sets (a symbol whose type the
        # producer never recorded), so without this a single undeclared
        # `notype` export would be reported twice, once under each kind. The
        # function tier runs first and keeps it.
        already_reported = {c.symbol for c in changes}

        for mangled in sorted(gained):
            if mangled in declared or mangled in declared_names:
                continue
            if mangled in already_reported:
                continue
            declaration = new_map.get(mangled)
            changes.append(
                make_change(
                    kind,
                    symbol=mangled,
                    # `name` is what the kind's description_template renders;
                    # the export table carries only the mangled spelling, so
                    # that is the honest value for both. `new` additionally
                    # populates `Change.new_value`, which the addition
                    # renderers read.
                    name=mangled,
                    new=mangled,
                    entity_id=(
                        declaration.entity_id if declaration is not None else None
                    ),
                )
            )
    return changes
