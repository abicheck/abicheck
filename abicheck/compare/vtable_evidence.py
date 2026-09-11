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
pre-check belongs here.** Attempted (round 2), found to regress real
detection coverage (round 3 reverted it) -- a blanket "not is_present"
pre-check cannot tell PDB's own structural non-evidence apart from a
hand-constructed/typed-API ``RecordType`` omitting ``vtable=`` (both
resolve to ``NOT_COLLECTED``). See ``diff_types_vtable.py``'s own module
docstring (its "Track 4, 5B final closure" section) for the full
three-round account.

**T9 closure (duplication-and-convergence-assessment.md Phase 6 item 4,
this revision): the PDB fabrication that closure left open is now closed,
narrower than round 2's attempt.** Round 2's mistake was checking
``not is_present`` — a status a hand-built fixture's own omission
convention can produce too. The fix is not a broader status check; it is
a *narrower and more precise* one, gated on ``FactStatus.UNSUPPORTED``
specifically (see :func:`vtable_transition_is_evidenced`'s own body) —
the one status ``model/fact.py`` already reserves for "this producer
cannot express this family at all," which a public dataclass's own
omission-resolution path (``bridge_legacy_and_fact``) never produces on
its own; only an explicit ``Fact.unsupported()`` construction does. The
other half of the fix is at the producer boundary, not here:
``pdb_model.py``'s ``_record_from_layout`` now constructs every PDB
record's ``vtable_fact``/``vptr_offset_bits_fact`` as an explicit
``Fact.unsupported(..., producer="pdb")`` instead of omitting the field
and falling back to the ambiguous default. Together, this closes the
reachable, confirmed fabrication round 2 found (an apparent vtable
transition read off a PDB-derived side) while leaving every existing
``NOT_COLLECTED`` caller — including the leaf-class regression round 3's
revert protects — on the exact heuristic this module already had.

**T9 second slice (this revision): the DWARF per-translation-unit
completeness gap the T9 closure above left open.** The PDB slice answers
"can this producer express the family at all" (a per-*producer*
capability claim). It cannot answer the question this slice closes: DWARF
genuinely CAN express bases/virtual_bases/vtable, and usually does
completely, but ``dwarf_snapshot.py``'s own "first definition wins" ODR
handling (``_DwarfSnapshotBuilder._check_and_register_type_name``)
builds each record type from exactly ONE
compilation unit's own view of it and silently discards every other CU's
own copy -- including whatever that CU independently saw about the same
class's virtual methods and bases. When two CUs compiled from the same
header genuinely disagree (a differing ``-g`` level, a TU that never used
a given virtual so the compiler omitted its DIE, ``-flimit-debug-info``
trimming), the retained definition's own evidence is real but may not be
*complete* -- the exact ambiguity this module's own
:func:`vtable_transition_is_evidenced` docstring already names as an
unguarded false-positive source.

``extract.dwarf_vtable_completeness`` is the producer
half: it compares every discarded ODR-duplicate DIE's own bases/
virtual_bases/vtable membership against the retained definition's, and
downgrades the *disagreeing* sibling fact(s) to ``Fact.partial(...)``
(never a new status -- ``PARTIAL``'s own docstring, "covered only part of
the requested scope... the uncovered part is unknown, not absent,"
already states exactly this claim) -- see that module's own "T9 third
slice" docstring note for why this is per-*field*, not a blanket
per-record decision. This function's own decline check, below, now
includes ``PARTIAL`` alongside ``UNSUPPORTED`` for ``vtable_fact``, for
the same reason the PDB slice declined on ``UNSUPPORTED``: an
evidence-completeness gap, once flagged, must not let a difference
derived from it read as a real change. Unlike ``UNSUPPORTED`` (a blanket,
producer-wide claim), ``PARTIAL`` here is per-record and DWARF-specific
-- it says nothing about any *other* record in the same snapshot, and
nothing about what a non-DWARF producer would report for the same class.

``diff_cxx_rules._transitive_bases`` needed no code change for this slice
-- it already reads ``bases_fact``/``virtual_bases_fact`` via
``_fact_str_list_confirmed``, which already treats anything other than
``PRESENT`` (``PARTIAL`` included) as "not confirmed complete." Producing
``PARTIAL`` for those two fields is what activates a gate that was already
there, not a new one.

**T9 third slice (this revision): scoping this function's own decline
check to match the producer's per-field narrowing (Codex review finding
on this PR).** The paragraph above originally had this function's
top-level decline check gate on ``bases_fact``/``virtual_bases_fact``
too, alongside ``vtable_fact`` -- reasoning that a per-TU gap on ANY of
the three siblings cast doubt on ALL of a record's own DWARF-derived
evidence, since the producer downgraded all three together. That
premise no longer holds: the producer now downgrades only the field(s)
that actually disagreed, so a duplicate confined to (say) ``bases``
leaves ``vtable_fact`` genuinely ``PRESENT`` -- and the old blanket
decline check would still reject even direct, non-empty vtable evidence
for that record, losing a real ``TYPE_VTABLE_CHANGED``/
``VIRTUAL_METHOD_ADDED`` finding for a reason unconnected to vtable
evidence at all. This function never reads ``bases``/``bases_fact`` in
the first place (only ``diff_cxx_rules._transitive_bases`` does, via the
paragraph above), so it was never protecting anything of its own; the
top-level check now gates only on ``vtable_fact``, and the ``virtual_
bases_fact``-dependent fallback comparison at the very end of this
function gates on ``virtual_bases_fact`` at its own point of use instead
-- each field's completeness gap now blocks only the branch(es) that
actually consult it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ..model import FactStatus, Function, RecordType, resolved_fact_value

OwnerClassOf = Callable[[Function], "str | None"]
NamespaceSuffixSpellings = Callable[[str], "list[str]"]

#: Statuses on which `vtable_transition_is_evidenced` declines outright --
#: see that function's own body for the full account of each member's own
#: reason (UNSUPPORTED: producer-wide incapability, e.g. PDB;
#: PARTIAL: DWARF's own per-translation-unit completeness gap, T9).
_DECLINE_STATUSES = (FactStatus.UNSUPPORTED, FactStatus.PARTIAL)


def vtable_fact_declined(t_old: RecordType, t_new: RecordType) -> bool:
    """Whether either side's ``vtable_fact`` status is one of
    ``_DECLINE_STATUSES`` (``UNSUPPORTED``/``PARTIAL``) -- the exact
    top-level check :func:`vtable_transition_is_evidenced` gates on, split
    out so a caller other than that function's own ``TYPE_VTABLE_CHANGED``
    consumer can ask the identical question.

    ``diff_cxx_rules.virtual_method_addition`` needs this directly (Codex
    review finding on this PR): its own fallthrough path assumed "when
    :func:`vtable_transition_is_evidenced` returns ``False``, either the
    raw arrays genuinely don't evidence anything, or a *genuinely new*
    mangled symbol would always have been caught by that function's own
    'class's own virtual functions' branch regardless" -- an invariant
    this module's own ``PARTIAL``/``UNSUPPORTED`` top-level short-circuit
    now breaks by construction: it returns ``False`` *before* the
    owned-virtual-signature branch ever runs, even for a class that
    genuinely gained a new virtual method. Without its own explicit check
    here, ``virtual_method_addition`` would fall through to its
    override-signature check and -- finding no matching override, since
    bases/virtual_bases can be completely evidenced even while vtable
    itself is PARTIAL -- emit a BREAKING ``VIRTUAL_METHOD_ADDED`` for
    exactly the capture-gap artifact this whole T9 closure exists to
    suppress, just through the sibling detector instead of the primary
    one.
    """
    return (
        t_old.vtable_fact is not None and t_old.vtable_fact.status in _DECLINE_STATUSES
    ) or (
        t_new.vtable_fact is not None and t_new.vtable_fact.status in _DECLINE_STATUSES
    )


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
    virtual functions" branch **when this function's own top-level decline
    check above did not already short-circuit before reaching it** -- a
    genuinely new mangled symbol is always present in the new side's
    owned-signature set and absent from the old side's, so this predicate
    returns ``True`` for it whenever it gets that far. It does NOT always
    get that far: the T9 ``PARTIAL`` addition to the top-level decline
    check (unlike the original ``UNSUPPORTED``-only gate, which only ever
    fires for PDB, where ``Function.is_virtual`` is never set at all, so
    ``virtual_method_addition`` returns at its very first line before
    reaching any of this) is reachable for DWARF records where a genuinely
    new virtual method's own symbol legitimately exists -- so
    ``virtual_method_addition`` can no longer simply defer to this
    predicate's own ``False`` meaning "not evidenced, safe to fall through
    to my own override check": ``False`` can now also mean "declined
    without ever consulting the owned-signature evidence at all." See
    :func:`vtable_fact_declined` (Codex review finding on this PR) for the
    caller-side fix -- ``virtual_method_addition`` now checks it directly,
    rather than relying on this predicate's return value alone to imply
    it. A previous revision tried to close the pure-virtual accepted false
    negative here directly by reading ``vptr_offset_bits`` -- see the
    body for why that witness is circular and made this guard inert.
    Closing that one for real needs evidence the model does not carry (a
    per-finding provider record, or a polymorphism walk over both base
    chains) -- see AGENTS.md's evidence-provider entry -- not a cleverer
    reading of the fields already here.
    """
    if vtable_fact_declined(t_old, t_new):
        # ADR-063 Track 4 5B final closure / T9: `UNSUPPORTED` is not the
        # generic "not is_present" pre-check round 2 landed and round 3
        # reverted (see the module docstring's "5B final closure" note) --
        # it is the one status a producer's own structural incapability
        # explicitly claims (`Fact.unsupported()`, e.g. `pdb_model.py`'s
        # PDB-derived records), and a hand-constructed/typed-API
        # `RecordType` omitting `vtable=` never resolves to it (that
        # omission backfills to `NOT_COLLECTED` via `bridge_legacy_and_fact`
        # -- see model/fact.py's own "producer" docstring). So gating here,
        # specifically on `UNSUPPORTED` and nowhere else, closes the real,
        # reachable PDB fabrication (an apparent vtable transition read off
        # a side that can never report vtable evidence at all, whether via
        # the size/base fallback below or via the owned-virtual-function
        # fallback -- PE/PDB also never set `Function.is_virtual` for a
        # confirmed reason, so that stream is equally untrustworthy from an
        # `UNSUPPORTED` side) without reintroducing the regression round 3
        # found: `NOT_COLLECTED` (a hand-built fixture's own "no virtuals"
        # convention, or any other producer's genuine non-evidence) is
        # untouched and keeps falling through to the heuristics below,
        # exactly as before this check existed.
        #
        # `PARTIAL` (T9 second slice) closes the sibling gap `UNSUPPORTED`
        # alone cannot: DWARF's own per-translation-unit completeness
        # signal (`extract.dwarf_vtable_completeness`) -- an ODR-duplicate
        # DIE in another CU that disagreed with the retained definition's
        # own vtable membership specifically. Unlike `UNSUPPORTED` (a
        # producer-wide incapability claim, true for every record that
        # producer ever emits), `PARTIAL` is per-record, per-*field*, and
        # DWARF-specific: it says this one class's `vtable` evidence, in
        # THIS snapshot, may not be the complete set -- not that DWARF as a
        # format cannot express the family, and (T9 third slice, this
        # revision) not anything about a *different* field of the same
        # record that never actually disagreed (`extract.
        # dwarf_vtable_completeness.finalize_vtable_evidence_completeness`
        # downgrades each of bases/virtual_bases/vtable independently now,
        # not as a blanket per-record decision -- see that module's own
        # "Downgrades are scoped per disagreeing field" docstring note;
        # Codex review finding on this PR caught the mismatch between that
        # producer-side narrowing and this function still gating on
        # `bases_fact`/`virtual_bases_fact` wholesale here).
        #
        # `bases_fact`/`virtual_bases_fact` are deliberately NOT part of
        # this top-level check: this function never reads `bases`/
        # `bases_fact` at all, and `virtual_bases_fact` is consulted only
        # by the size/virtual-bases fallback at the very end of this
        # function, which gates on it there, directly at its own point of
        # use, instead of over the whole function. `vptr_offset_bits_fact`
        # is deliberately NOT part of either check -- see the "NOT
        # consulted here" note further down this docstring for why that
        # field needs its own separate treatment.
        return False
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
    # T9 third slice: gated here, at this fallback's own point of use,
    # rather than over the whole function (see the top-level check's own
    # updated comment) -- a `virtual_bases_fact` completeness gap only
    # makes THIS comparison unsafe (an incomplete list can differ from a
    # complete one for reasons that have nothing to do with a real base
    # change), it says nothing about the vtable-evidence branches already
    # returned above.
    if (
        t_old.virtual_bases_fact is not None
        and t_old.virtual_bases_fact.status in _DECLINE_STATUSES
    ) or (
        t_new.virtual_bases_fact is not None
        and t_new.virtual_bases_fact.status in _DECLINE_STATUSES
    ):
        return False
    old_virtual_bases = resolved_fact_value(t_old.virtual_bases_fact, [])
    new_virtual_bases = resolved_fact_value(t_new.virtual_bases_fact, [])
    return list(old_virtual_bases) != list(new_virtual_bases)
