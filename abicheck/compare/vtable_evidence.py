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

"""Whether an *empty<->non-empty* ``RecordType.vtable`` transition rests on
real evidence or a capture gap -- the one predicate ``diff_types_vtable``'s
``TYPE_VTABLE_CHANGED`` detector and ``diff_cxx_rules.virtual_method_addition``
must now agree on, instead of each carrying its own copy of the reasoning
(ADR-063 Track 2, 5B closure).

**Why this module exists.** Before this split, ``virtual_method_addition``
declined outright (deferring to ``TYPE_VTABLE_CHANGED``) whenever the two
sides' ``vtable`` arrays merely differed, *without ever checking* that
``diff_types_vtable`` would actually fire. That was safe only in the one
shape both docstrings called out by name -- one side's ``vtable_fact``
uncollected, the other genuinely populated -- because ``diff_types_vtable``'s
own heuristic happens to still find evidence there. Anywhere else the two
sides' evidence agrees (same owned-virtual-function set, same size, same
virtual-base list) ``diff_types_vtable`` stays silent too, and
``virtual_method_addition`` was deferring to a detector that was never going
to fire -- silently dropping the one coverage this function exists to
provide, with nothing but a paragraph in each module's docstring holding the
two in sync.

Closing that gap for real means ``virtual_method_addition`` must call the
*same* evidence predicate ``diff_types_vtable`` uses, not just trust it by
convention. Doing that directly (importing ``diff_types_vtable`` into
``diff_cxx_rules``) is impossible: ``diff_types_vtable.py`` already imports
``diff_cxx_rules`` for ``vtable_slot_is_override_reuse``, so the reverse
import would be a cycle -- exactly the constraint both modules' docstrings
recorded as the blocker. The fix is the usual one for two leaves that need
the same logic: move the shared predicate to a module *below* both of them,
so each imports downward and neither imports the other.

That predicate -- ``vtable_transition_is_evidenced`` (moved here from
``diff_types_vtable._vtable_transition_is_evidenced`` verbatim, plus its
``_owned_virtual_signatures`` helper) -- itself needs an owner-name
resolver (``diff_cxx_rules.owner_class_of``) and a namespace-suffix
matcher (``type_reachability_spelling._namespace_suffix_spellings``). Both
of those already sit *above* this candidate leaf in the dependency graph
(``type_reachability_spelling`` itself imports ``diff_cxx_rules``), so
importing either one here would recreate the identical cycle one level
down. Per ``compare/AGENTS.md`` this package may depend on ``model`` only,
which settles it independent of the cycle concern anyway: both functions
below take ``owner_class_of``/``namespace_suffix_spellings`` as **injected
callables** rather than importing them, so this module's own dependency
stays exactly ``model`` and nothing else. Each caller (``diff_cxx_rules``,
``diff_types_vtable``) supplies its own already-available implementations.

Docstrings quoting the FP-history and design rationale below are carried
over unchanged from ``diff_types_vtable.py`` -- only the owner/namespace
lookups became parameters; no behavior changed for ``diff_types_vtable``'s
own existing callers.

**ADR-063 Track 4, 5B final closure: whether a direct ``FactStatus``
pre-check belongs here.** Attempted, found to regress real detection
coverage, reverted -- see ``diff_types_vtable.py``'s own module docstring
(its "Track 4, 5B final closure" section) for the full three-round
account, which is the canonical writeup for this finding rather than
repeated here. In short: a whole-comparison decline whenever either
side's ``vtable_fact`` was not ``is_present`` did close a real,
confirmed-reachable fabrication for a PDB-derived side -- but ``vtable``
is a public, positional ``RecordType`` field whose own omission at
construction (not just PDB's) also resolves to ``NOT_COLLECTED`` via
``bridge_legacy_and_fact``, which is exactly how a large fraction of this
codebase's own hand-constructed test fixtures (and, by the same public
constructor, any external typed-API caller) spell "this class has no
vtable" for an ordinary non-polymorphic class. The decline could not tell
that shape apart from PDB's, and silently regressed
``tests/test_abicc_scenario_parity.py::TestLeafClassVirtualMethodAdditions
::test_virtual_added_to_leaf_class``, a previously-passing scenario. This
function's heuristic is therefore unchanged from before this closure --
the PDB fabrication remains a real, open, and now-explicitly-documented
gap (see the plan's own 5B note), not a hypothetical, but closing it
needs a snapshot/producer-level signal analogous to
``AbiSnapshot.clang_vtable_facts_reliable`` (something that can tell "this
whole backend never captures vtable data" apart from "this one record's
constructor omitted the field"), not a per-record ``FactStatus`` branch.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ..model import Function, RecordType, resolved_fact_value

OwnerClassOf = Callable[[Function], "str | None"]
NamespaceSuffixSpellings = Callable[[str], "list[str]"]


def _owned_virtual_signatures(
    name: str,
    funcs: Mapping[str, Function],
    *,
    owner_class_of: OwnerClassOf,
    namespace_suffix_spellings: NamespaceSuffixSpellings,
) -> set[str]:
    """The mangled names of *name*'s own virtual member functions.

    An evidence stream independent of ``RecordType.vtable``: these come from
    ``snapshot.functions`` (their own DIEs / AST nodes), not from the class
    DIE's virtual-method children, so one going missing does not take the
    other with it.
    """

    # An *exact* comparison was wrong, in the direction that silences
    # findings (Codex review). CastXML records a namespaced class under its
    # bare leaf (`A`) while `owner_class_of` reconstructs the qualified
    # `ns::A` from the mangled method, so the two never met, both signature
    # sets came back empty, and the guard fell through to the size check --
    # suppressing e.g. a class losing its last private virtual with no size
    # change, which no other detector reports.
    #
    # Matched through `namespace_suffix_spellings` (depth-aware, so a
    # template argument's own `::` isn't mistaken for a namespace boundary).
    # Deliberately *eager*: a spurious match makes the two sides' sets
    # differ, which keeps the finding -- the only safe direction here.
    wanted = {name, *namespace_suffix_spellings(name)}

    def _owns(fn: Function) -> bool:
        owner = owner_class_of(fn)
        if not owner:
            return False
        return bool(wanted & {owner, *namespace_suffix_spellings(owner)})

    return {
        mangled
        for mangled, fn in funcs.items()
        if getattr(fn, "is_virtual", False) and _owns(fn)
    }


def vtable_transition_is_evidenced(
    name: str,
    t_old: RecordType,
    t_new: RecordType,
    old_funcs: Mapping[str, Function],
    new_funcs: Mapping[str, Function],
    *,
    owner_class_of: OwnerClassOf,
    namespace_suffix_spellings: NamespaceSuffixSpellings,
) -> bool:
    """Whether an *empty<->non-empty* vtable difference rests on real evidence.

    ``RecordType.vtable`` cannot express "not captured": it is a plain list,
    and on the DWARF path it is simply the class's own virtual-method DIEs in
    child order (``dwarf_snapshot._process_virtual_method_child``). So an
    empty list means either "this class has no virtuals of its own" *or*
    "this side's debug info did not carry them" -- and the two are
    indistinguishable from the list alone.

    That ambiguity produced a real false positive: identical headers on both
    sides, no DWARF vtable, and not one ``_ZTV`` symbol anywhere still
    emitted ``TYPE_VTABLE_CHANGED`` as BREAKING, because one side's virtual
    methods happened to live in a translation unit only the other side's
    debug info covered (differing ``-g`` level, a differently-inlined TU, or
    ODR first-definition-wins in ``dwarf_snapshot``). The neighbouring
    ``diff_vtable_layout`` already names this exact hazard for its own
    detector and answers it with a tri-state ``None``; ``diff_elf_layout``
    answers it by only ever comparing a ``_ZTV`` present on *both* sides.
    This is the same principle applied to the type-level detector: degrade to
    silence rather than fabricate a break.

    An independent *layout* signal is what makes the transition real. A class
    that genuinely gains its first virtual function also gains a vptr, so it
    grows; one that gains or loses a virtual base says so directly. When
    neither moved and both sizes are known, no real polymorphism change can
    have occurred and the differing list is capture noise.

    Size alone is **not** sufficient, which is why the class's own virtual
    functions are consulted first (they are a different projection of the
    same debug info, not a fully independent one -- see the body). A sufficiently over-aligned class absorbs
    its new vptr into existing padding: verified against g++, both
    ``struct alignas(8) A {}`` and ``struct alignas(8) A { virtual void f(); }``
    are 8 bytes, as are the ``alignas(16)`` pair at 16 -- so a size-only guard
    suppressed a genuine first-vptr addition (Codex review). It compounded:
    ``diff_cxx_rules.virtual_method_addition`` withholds
    ``VIRTUAL_METHOD_ADDED`` whenever the vtable lists differ *and this
    predicate says the difference is evidenced*, so a run whose only
    evidence was this false positive was left with a compatible ``FUNC_ADDED``
    and a ``COMPATIBLE`` verdict on a real layout break. ``snapshot.functions``
    is a separate evidence stream from the class DIE's virtual-method
    children, so it answers that case without weakening the capture-gap
    guard.

    Deliberately conservative in the other direction: an *unknown* size on
    either side corroborates nothing but also refutes nothing, so the finding
    is kept. The suppression needs positive evidence that layout held still;
    it is not a fallback for missing information.

    Two known false negatives, accepted rather than papered over -- both are
    a class whose vtable grows while its object size does not, which is
    indistinguishable from capture noise on the evidence this detector
    receives:

    * A class already polymorphic through a base, declaring no virtuals of
      its own, that gains one.
    * An over-aligned class gaining its first *pure* virtual. A pure virtual
      has no out-of-line definition, so ``dwarf_snapshot`` drops its
      declaration-only DIE from ``snapshot.functions`` while still counting
      it as a vtable child -- both owned-signature sets read empty -- and
      ``alignas`` absorbs the new vptr into existing padding so the size
      does not move either (reproduced against g++ with
      ``struct alignas(8) A { virtual void f() = 0; }``).

    Neither loses the *break*: ``diff_layout._check_vptr_introduced`` fires
    independently on the same None -> 0 vptr transition and the verdict stays
    BREAKING. Only this predicate's own ``TYPE_VTABLE_CHANGED`` is withheld.
    The 5B closure this module implements does not reach either accepted
    false negative above: the second bullet's pure virtual has no linkable
    definition at all, so ``diff_cxx_rules.virtual_method_addition`` is never
    even called for it (nothing for ``_diff_functions``'s own loop to
    iterate over); the first bullet, when it involves a real, linkable
    virtual method, is *evidenced* by this predicate's own "class's own
    virtual functions" branch (a genuinely new mangled symbol is always
    present in the new side's owned-signature set and absent from the old
    side's), so ``virtual_method_addition`` correctly defers to this
    predicate rather than needing its own fallthrough -- it is this
    predicate's own remaining gap to close, not a gap in the symbol-level
    caller's own coupling to it. Leaning on a sibling detector is not a
    comfortable place to be, and a previous revision tried to close the
    second case here directly by reading ``vptr_offset_bits`` -- see the
    body for why that witness is circular and made this guard inert.
    Closing it for real needs evidence the model does not carry (a
    per-finding provider record, or a polymorphism walk over both base
    chains) -- see AGENTS.md's evidence-provider entry -- not a cleverer
    reading of the fields already here.
    """
    old_vtable = resolved_fact_value(t_old.vtable_fact, [])
    new_vtable = resolved_fact_value(t_new.vtable_fact, [])
    if old_vtable and new_vtable:
        # Both sides captured something, so the difference is a real
        # reorder/replace rather than one side's evidence going missing.
        return True
    if _owned_virtual_signatures(
        name,
        old_funcs,
        owner_class_of=owner_class_of,
        namespace_suffix_spellings=namespace_suffix_spellings,
    ) != _owned_virtual_signatures(
        name,
        new_funcs,
        owner_class_of=owner_class_of,
        namespace_suffix_spellings=namespace_suffix_spellings,
    ):
        # The class's own virtual *functions* -- a different projection of
        # the debug info from `RecordType.vtable`, and the signal that keeps
        # an over-aligned class honest when the size check below cannot.
        #
        # Not fully independent, and the docstring used to overclaim that:
        # on the DWARF path both ultimately derive from `DW_TAG_subprogram`
        # evidence, so a TU whose coverage vanishes can take the vtable list
        # *and* the function with it (Codex review). When that happens the
        # sets differ, this returns True, and the finding is kept -- i.e. the
        # guard declines to suppress rather than suppressing wrongly. That is
        # the failure direction to have: it leaves the pre-existing false
        # positive standing instead of hiding a real break. Closing it needs
        # artifact or provenance evidence (`_ZTV` presence, per-finding
        # providers) the type-level detector does not yet receive.
        return True
    # NOT consulted here: ``vptr_offset_bits``. It reads like the one
    # independent layout witness available, and a previous revision of this
    # function used it as exactly that -- wrongly. At the time, both
    # producers assigned it as ``0 if vtable else None`` (``dwarf_snapshot.
    # py``, ``dumper_castxml.py``), so on those two backends
    # ``(old.vptr_offset_bits is None) != (new.vptr_offset_bits is None)``
    # was *identical* to the empty-vs-non-empty vtable transition being
    # guarded: true by construction for every input reaching this point,
    # which silently made the whole guard a no-op and let the original
    # capture-gap false positive straight back through (Codex review).
    # ``dumper_castxml.py`` still assigns it exactly that way. ``dwarf_
    # snapshot.py`` no longer does (G31 Phase C): it now reads a real
    # ``_vptr.<Class>``/base-chain offset from DWARF in the common case,
    # falling back to the same ``0 if vtable`` heuristic only for the
    # residual unresolved set -- so for DWARF the field is no longer purely
    # circular. This function still doesn't consult it, on purpose:
    # declining to use an available signal is always safe (the failure mode
    # this guard exists to avoid only ever ran the other way -- trusting a
    # circular signal AS IF independent), and using it as a genuine witness
    # for the now-partially-real DWARF case while still excluding it for
    # castxml's own still-fully-circular case is its own careful design +
    # FP-verification effort, not a drive-by extension here — see
    # ``tests/test_vtable_evidence_guard.py``'s own note on why
    # ``abicheck.dwarf_snapshot`` was dropped from its premise-pin test.
    # Only the optional ``ABICHECK_CLANG_LAYOUT_TOOL`` path computes it from
    # a real layout query on the castxml/clang side, and nothing in the
    # model distinguishes that value from the derived one -- so it still
    # cannot be trusted as evidence here at all on that side.
    if t_old.size_bits is None or t_new.size_bits is None:
        return True
    if t_old.size_bits != t_new.size_bits:
        return True
    old_virtual_bases = resolved_fact_value(t_old.virtual_bases_fact, [])
    new_virtual_bases = resolved_fact_value(t_new.virtual_bases_fact, [])
    return list(old_virtual_bases) != list(new_virtual_bases)
