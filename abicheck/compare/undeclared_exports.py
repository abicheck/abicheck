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

"""Export-table-only additions *and* removals (``*_added_elf_only`` /
``*_removed_elf_only``).

A sibling of :mod:`abicheck.diff_symbols` rather than a function inside it:
that module is at the AI-readiness file-size cap, and ``AGENTS.md``'s rule
for a file at the cap is to move responsibility out to an owned module, not
to trim the file to fit. This detector owns one question -- did the set of exports that no public
header declares change -- and reads only the two sides' already-collected
ELF export tables to answer it, in both directions. It performs no
extraction and no second comparison of its own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..detector_registry import registry
from ..diff_helpers import make_change
from ..diff_symbols_renames import _should_filter_transitive_runtime_symbols
from ..elf_symbol_filter import (
    ALL_SYMBOL_TYPES,
    FUNCTION_SYMBOL_TYPES,
    VARIABLE_SYMBOL_TYPES,
    exported_symbol_names,
)
from ..model.change_catalog.kinds import ChangeKind
from .export_owner_resolution import special_member_export_coverage

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

    The removal direction used to be deliberately absent, on the reasoning
    that asserting a break from export-table evidence alone, on a symbol the
    headers never promised, was an unproven finding and that
    ``exported_not_public``'s ``RESOLVED`` state already showed the
    disappearance. Measurement falsified both halves of that. A C++ class's
    vtable and typeinfo (``_ZTV``/``_ZTI``/``_ZTS``/``_ZTT``) are *exactly*
    this shape -- compiler-emitted, declared by no header, and bound by every
    old consumer that constructs, destroys, ``dynamic_cast``es or throws the
    type. Losing that export breaks those consumers while the class
    declaration and its entire method list survive untouched, so nothing in
    the header-derived diff can see it, and a ``RESOLVED`` hygiene state is
    not a binary-break population. On a controlled fixture whose only change
    was localizing ``_ZTV3Foo``, ``compare old.so new.so`` reported BREAKING
    (exit 4) and the same inputs with ``-H`` reported NO_CHANGE (exit 0) with
    zero findings -- adding relevant headers *erased* a supported public
    binary-contract loss, which is the one thing more evidence must never do.

    So both directions are emitted, and the asymmetry that remains is in the
    *claim*, not the direction: an addition says the export set grew, a
    removal says a symbol old consumers could bind to is gone. Neither
    decides relevance here. A removal is emitted as the weak,
    export-table-only kind and then passes through the ordinary pipeline --
    public-surface scoping (``surface.py``, which resolves a special name's
    owning type through ``itanium_special_name_owner_identifiers`` and
    demotes a finding whose every resolvable owner candidate is confidently
    private), contract evaluation, suppression and policy -- exactly as any
    other change does. That is deliberate: an internal helper that leaked
    into the export table and was then properly hidden is a *fix*, and the
    machinery that already knows how to say so is the machinery that says so
    for every other finding, not a second private copy of it here. An owner
    that cannot be resolved stays reported, per this repository's
    unresolved-coverage semantics: unknown relevance is not a clean result.
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
    additions_visible_here = not getattr(new, "elf_only_mode", False)

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
    #
    # `ALL_SYMBOL_TYPES`, not the union of the two class sets: that union
    # omits `other`, the bucket an unrecognised `st_info` type lands in, so
    # an `OTHER -> FUNC` rename-in-place still read as a brand-new export
    # (Codex review, a round after the first). The question here is about a
    # *name*, not a kind, so it takes every type the parser can produce --
    # derived from the enum rather than listed, so the next type added does
    # not need a fourth round.
    old_export_names = exported_symbol_names(
        old_elf,
        ALL_SYMBOL_TYPES,
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
    # The addition half only: a headerless NEW already reports added
    # exports through the ordinary function diff (see above). The removal
    # half below is guarded separately, off OLD, because a removed symbol
    # is the one present in OLD.
    if additions_visible_here:
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
    changes.extend(
        _export_only_removals(old, new, old_elf, new_elf, filter_transitive, changes)
    )
    return changes


def _export_only_removals(
    old: AbiSnapshot,
    new: AbiSnapshot,
    old_elf: object,
    new_elf: object,
    filter_transitive: bool,
    already: list[Change],
) -> list[Change]:
    """The mirror of the addition loop: ABI-relevant exports OLD carried that
    NEW's export table no longer has, and that no public header declares on
    either side.

    Guarded off OLD's mode, not NEW's: the symbol at issue is the one present
    in OLD, so the ordinary diff can name it only if it reached OLD's map,
    which ``dumper_elf_fallback`` does only for a headerless OLD. In the
    mixed shape (headerless OLD, header-aware NEW) the ordinary diff already
    reports the removal and this half must stay quiet; in the shape this
    exists for -- header-aware on both sides -- the symbol is in no map
    anywhere and nothing else can see it at all.

    Emits the weak, export-table-only kinds and decides no relevance itself;
    see the detector docstring for why that judgement stays with
    public-surface scoping, contract evaluation and policy rather than being
    re-implemented here.
    """
    if getattr(old, "elf_only_mode", False):
        return []
    # Symmetric with the addition half's `old_export_names`, and load-bearing
    # for the same reason: a symbol whose ELF type changed in place
    # (`STT_OBJECT` -> `STT_FUNC`) is absent from the per-class `new_exports`
    # but is not gone -- `symbol_type_changed` already describes it, and
    # calling it a removal too would both double-report and inflate the
    # break population. Taken across `ALL_SYMBOL_TYPES` so an unrecognised
    # `st_info` type ("other") cannot slip through the union of the two
    # class sets.
    new_export_names = exported_symbol_names(
        new_elf,
        ALL_SYMBOL_TYPES,
        abi_relevant_only=True,
        filter_transitive_runtime_symbols=filter_transitive,
    )
    removals: list[Change] = []
    already_reported = {c.symbol for c in already}
    for symbol_types, declaring_maps, kind in (
        (
            FUNCTION_SYMBOL_TYPES,
            (old.function_map, new.function_map),
            ChangeKind.FUNC_REMOVED_ELF_ONLY,
        ),
        (
            VARIABLE_SYMBOL_TYPES,
            (old.variable_map, new.variable_map),
            ChangeKind.VAR_REMOVED_ELF_ONLY,
        ),
    ):
        old_exports = exported_symbol_names(
            old_elf,
            symbol_types,
            abi_relevant_only=True,
            filter_transitive_runtime_symbols=filter_transitive,
        )
        lost = old_exports - new_export_names
        if not lost:
            continue
        old_map, new_map = declaring_maps
        declared = set(old_map) | set(new_map)
        declared_names = {d.name for d in old_map.values()} | {
            d.name for d in new_map.values()
        }
        for mangled in sorted(lost):
            # Declared by either side's headers -> the ordinary function or
            # variable diff owns it, under the strong kind that knows what
            # the declaration said. Same rule as the addition half.
            if mangled in declared or mangled in declared_names:
                continue
            # The same rule, resolved structurally rather than by literal
            # name, for the one declaration shape a name match cannot reach:
            # a ctor/dtor never enters `function_map` under any Itanium
            # mangling. `special_member_export_coverage` states what it
            # resolved and why (see its own docstring); only its `covered`
            # verdict -- a uniquely-resolved, non-template, inline special
            # member whose declaration is unchanged on both sides -- drops a
            # finding. Every other state keeps it and lets public-surface
            # scoping, contract evaluation and policy decide relevance.
            if special_member_export_coverage(mangled, old, new).covered:
                continue
            # `notype` is in both symbol-type sets; the function tier runs
            # first and keeps it, exactly as in the addition half.
            if mangled in already_reported:
                continue
            already_reported.add(mangled)
            declaration = old_map.get(mangled)
            removals.append(
                make_change(
                    kind,
                    symbol=mangled,
                    # Explicit, because `func_removed_elf_only` registers no
                    # `description_template` (its other producer,
                    # `diff_symbols`, has a declaration to phrase one from and
                    # passes its own). Omitting it made `make_change` raise
                    # `ValueError` for an undeclared *function* export loss --
                    # a crash, not a missing finding, and invisible to a test
                    # sweep that only localized data symbols.
                    description=(
                        f"Exported symbol not declared in any public header "
                        f"removed: {mangled}"
                    ),
                    # The export table carries only the mangled spelling, so
                    # that is the honest value; `old` is what the removal
                    # renderers read, mirroring the addition half's `new`.
                    name=mangled,
                    old=mangled,
                    entity_id=(
                        declaration.entity_id if declaration is not None else None
                    ),
                )
            )
    return removals
