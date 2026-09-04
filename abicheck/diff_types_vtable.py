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

"""The ``TYPE_VTABLE_CHANGED`` evidence-gating cluster: whether an
empty<->non-empty vtable transition rests on real evidence or a capture
gap, and the correlation with ``diff_layout``'s ``LAYOUT_UNVERIFIABLE``.

Split out of ``diff_types.py`` to stay under its line-count cap (ADR-063
Phase 0's detector migration pushed it over) -- a genuine leaf module (must
not import from ``diff_types`` at all, to avoid an import cycle:
``diff_types.py`` imports ``_diff_type_vtable`` back for its own use, the
only symbol this module exports). Everything else here
(``_vtable_transition_is_evidenced``/``_vtable_transition_rests_on_
unresolved_evidence``/``_layout_evidence_is_unverifiable``/
``_owned_virtual_signatures``/``_owned_virtual_signatures_for_record``) is
private to this cluster and was already only ever called from within it.

**ADR-063 Phase 5B (vtable/vptr_offset_bits slice) re-audit, landed.** The
plan's own "Deliberately not attempted" note (5B's first PR) flagged this
cluster's evidence-gap guards -- built before ``Fact[T]`` existed, inferring
"evidence missing" indirectly from ``size_bits``/virtual-signature
heuristics rather than reading ``FactStatus`` directly -- as needing "its
own dedicated slice with equal scrutiny." That scrutiny landed a genuine,
safe direct-``FactStatus`` improvement in the two *sibling* vtable
detectors this cluster correlates with (``diff_layout._check_vptr_
introduced``'s own per-record old-side check; ``diff_vtable_layout._is_
polymorphic``'s own per-record check) -- both self-contained, with no
other detector's coverage assumption riding on their exact decline
condition.

**This cluster's own two guards were re-verified and deliberately left
unchanged.** ``_vtable_transition_is_evidenced``'s "both sides captured
something" branch and its class's-own-virtual-functions/size-delta
fallbacks are not, on inspection, a stand-in for a direct ``FactStatus``
check the way ``diff_layout``'s snapshot-wide ``vtable_facts_reliable``
flag was: DWARF's own vtable extraction (``dwarf_snapshot.py``) reports
``Fact.present([])`` -- genuinely ``PRESENT``, not ``NOT_COLLECTED`` --
for a class whose virtual methods happen to live in a translation unit
only the *other* side's debug info covers (the exact false-positive this
guard's own docstring documents fixing). ``FactStatus`` cannot see that
gap at all: from DWARF's own local perspective, per-TU coverage loss is
indistinguishable from a genuinely non-polymorphic class, so both sides
read ``PRESENT`` either way. A direct ``vtable_fact.status`` pre-check
would therefore not replace the heuristic here -- it would only catch the
disjoint case of a genuinely *uncollected* fact (``NOT_COLLECTED``/
``FAILED``), and adding it as an unconditional decline was found, on
tracing the actual call graph, to be **unsafe**: ``diff_cxx_rules.
virtual_method_addition`` defers to this cluster ("``TYPE_VTABLE_CHANGED``
covers this case") precisely in the one-side-uncollected/other-side-
populated shape, relying on today's heuristic (not a ``FactStatus`` read)
to still find real evidence there and fire. Declining outright on
``NOT_COLLECTED`` would silently desynchronize the two detectors and drop
coverage neither would then provide -- see ``virtual_method_addition``'s
own docstring for the full trace, including why fixing it properly (making
that function consult this cluster's own evidenced/not-evidenced verdict
before deferring) needs an import this leaf module's own no-cycle
constraint (above) does not allow without further restructuring. Recorded
here, not silently left implicit, per this repo's own "say so explicitly
and record the gap" convention -- the sub-phase's own "vtable... all five
fields gated" removal gate is therefore still open for ``vtable`` through
this specific cluster, even though the two sibling detectors it correlates
with are now gated (Codex review, fresh evidence: this cluster never reads
``vptr_offset_bits``/``vptr_offset_bits_fact`` at all -- see the "NOT
consulted here" comment in the body below -- so ``vptr_offset_bits``
itself carries no residual gap through this cluster; it is fully gated via
``diff_layout._check_vptr_introduced``'s own direct-status pre-check).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping

from .checker_policy import ChangeKind
from .checker_types import Change
from .diff_cxx_rules import vtable_slot_is_override_reuse
from .diff_helpers import make_change
from .model import Function, RecordType, resolved_fact_value


def _vtable_transition_is_evidenced(
    name: str,
    t_old: RecordType,
    t_new: RecordType,
    old_funcs: Mapping[str, Function],
    new_funcs: Mapping[str, Function],
) -> bool:
    """Whether an *empty↔non-empty* vtable difference rests on real evidence.

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
    ``VIRTUAL_METHOD_ADDED`` whenever the vtable lists differ, so the run was
    left with a compatible ``FUNC_ADDED`` and a ``COMPATIBLE`` verdict on a
    real layout break. ``snapshot.functions`` is a separate evidence stream
    from the class DIE's virtual-method children, so it answers that case
    without weakening the capture-gap guard.

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
    BREAKING. Only this detector's own ``TYPE_VTABLE_CHANGED`` is withheld.
    Leaning on a sibling detector is not a comfortable place to be, and a
    previous revision tried to close the second case here directly by reading
    ``vptr_offset_bits`` -- see the body for why that witness is circular and
    made this guard inert. Closing it for real needs evidence the model does
    not carry (a per-finding provider record, or a polymorphism walk over
    both base chains) -- see AGENTS.md's evidence-provider entry -- not a
    cleverer reading of the fields already here.
    """
    old_vtable = resolved_fact_value(t_old.vtable_fact, [])
    new_vtable = resolved_fact_value(t_new.vtable_fact, [])
    if old_vtable and new_vtable:
        # Both sides captured something, so the difference is a real
        # reorder/replace rather than one side's evidence going missing.
        return True
    if _owned_virtual_signatures(name, old_funcs) != _owned_virtual_signatures(
        name, new_funcs
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


def _vtable_transition_rests_on_unresolved_evidence(
    t_old: RecordType,
    t_new: RecordType,
    old_funcs: Mapping[str, Function],
    new_funcs: Mapping[str, Function],
) -> bool:
    """True exactly when a kept ``TYPE_VTABLE_CHANGED`` finding for this
    already-matched ``RecordType`` pair rests on the *same* "one side has no
    known size" evidence gap ``LAYOUT_UNVERIFIABLE`` (``diff_layout.py``)
    reports for the same type -- as opposed to a real, independently-evidenced
    signal.

    Only ever consulted after ``_vtable_transition_is_evidenced`` already
    returned True, so this mirrors that function's own branches to isolate
    which one supplied the evidence:

    * Both sides' vtables populated -> a real reorder/replace. Not this case.
    * The class's own virtual *functions* (a separate projection of the
      debug info) differ -> a real signal, however imperfect. Not this case.
    * A real ``size_bits`` delta, or a virtual or non-virtual base change ->
      a real layout signal. Not this case.
    * An *unknown* ``size_bits`` on either side -> the finding was kept only
      because an unresolved-evidence gap corroborates nothing and refutes
      nothing. This is the one case sharing ``LAYOUT_UNVERIFIABLE``'s own
      evidence gap, and the only one a caller should treat as demotable.

    Used by ``_diff_type_vtable`` to scope its correlation marker
    (``Change.vtable_covers_unverifiable_layout_gap``) precisely; the
    finding's own severity is never changed (an earlier design that demoted
    it via ``effective_verdict`` was reverted as unsafe -- see AGENTS.md's
    "Findings emitted from absent evidence" entry). Co-occurrence with an
    unrelated ``LAYOUT_UNVERIFIABLE`` finding on a *different* same-named
    type (a real risk when correlating by bare ``Change.symbol`` alone,
    since two distinct records can share a leaf name in different
    namespaces -- Codex review) is not a reason to mark a
    genuinely-evidenced vtable change, and neither is any of the three
    real-signal branches above (Codex review: an earlier revision of this
    marker fired on any co-occurring ``LAYOUT_UNVERIFIABLE`` regardless,
    wrongly tagging a real reorder/replace with both sides populated).
    """
    old_vtable = resolved_fact_value(t_old.vtable_fact, [])
    new_vtable = resolved_fact_value(t_new.vtable_fact, [])
    if old_vtable and new_vtable:
        return False
    # A single normalized identity for *both* sides, not each RecordType's
    # own (possibly unset) qualified_name independently -- t_old and t_new
    # already arrived here as one matched pair (TypeMap's own ambiguity-safe
    # bare-name fallback, upstream of this function), so a legacy snapshot
    # leaving qualified_name unset on one side must not desynchronize the
    # identity this function matches owners against on the other (Codex
    # review, fresh evidence).
    qualified = t_new.qualified_name or t_old.qualified_name or t_new.name or t_old.name
    if _owned_virtual_signatures_for_record(
        qualified, old_funcs
    ) != _owned_virtual_signatures_for_record(qualified, new_funcs):
        return False
    # A virtual-base change is real, independent evidence regardless of
    # whether size_bits is known on either side (Codex review): it is not
    # gated behind a known size in _vtable_transition_is_evidenced's own
    # size-known branch, and must not be treated as unresolved here either
    # -- otherwise a genuine hierarchy change (also separately reported via
    # TYPE_BASE_CHANGED/BASE_CLASS_VIRTUAL_CHANGED) could have its
    # TYPE_VTABLE_CHANGED half demoted to RISK merely because size_bits
    # happens to be unknown.
    old_virtual_bases = resolved_fact_value(t_old.virtual_bases_fact, [])
    new_virtual_bases = resolved_fact_value(t_new.virtual_bases_fact, [])
    if list(old_virtual_bases) != list(new_virtual_bases):
        return False
    # An ordinary (non-virtual) base addition, removal, or reorder is the
    # identical class of independent evidence -- diff_types._diff_type_bases
    # separately reports it as TYPE_BASE_CHANGED/BASE_CLASS_POSITION_CHANGED
    # regardless of size_bits, so this correlation must not tag the
    # co-occurring vtable finding as resting purely on an evidence gap just
    # because size_bits also happens to be unknown (Codex review, fresh
    # evidence -- the identical false-correlation risk the virtual_bases
    # check above already guards against, just for the non-virtual case).
    old_bases = resolved_fact_value(t_old.bases_fact, [])
    new_bases = resolved_fact_value(t_new.bases_fact, [])
    if list(old_bases) != list(new_bases):
        return False
    return t_old.size_bits is None or t_new.size_bits is None


def _layout_evidence_is_unverifiable(
    t_old: RecordType, t_new: RecordType, *, vtable_facts_reliable: bool
) -> bool:
    """True when ``diff_layout._check_layout_unverifiable``'s own
    asymmetric-evidence condition holds for this *exact*, already
    type-matched ``RecordType`` pair.

    Delegates to ``diff_layout._layout_evidence_asymmetric`` -- the exact
    predicate ``_check_layout_unverifiable`` itself evaluates -- rather than
    a hand-duplicated copy, so the two can never silently drift apart
    (Codex review). Consulted with the identical ``t_old``/``t_new``
    objects ``diff_layout.py``'s own detector sees for this type -- so
    there is no separate symbol-name correlation step (bare
    ``Change.symbol`` equality across two independently-emitted findings)
    that a same-named-but-different type could defeat.
    """
    from .diff_layout import _layout_evidence_asymmetric

    return _layout_evidence_asymmetric(
        t_old, t_new, vtable_facts_reliable=vtable_facts_reliable
    )


def _owned_virtual_signatures(name: str, funcs: Mapping[str, Function]) -> set[str]:
    """The mangled names of *name*'s own virtual member functions.

    An evidence stream independent of ``RecordType.vtable``: these come from
    ``snapshot.functions`` (their own DIEs / AST nodes), not from the class
    DIE's virtual-method children, so one going missing does not take the
    other with it.
    """
    from .diff_cxx_rules import owner_class_of
    from .type_reachability_spelling import _namespace_suffix_spellings

    # An *exact* comparison was wrong, in the direction that silences
    # findings (Codex review). CastXML records a namespaced class under its
    # bare leaf (`A`) while `owner_class_of` reconstructs the qualified
    # `ns::A` from the mangled method, so the two never met, both signature
    # sets came back empty, and the guard fell through to the size check --
    # suppressing e.g. a class losing its last private virtual with no size
    # change, which no other detector reports.
    #
    # Matched through `_namespace_suffix_spellings` (depth-aware, so a
    # template argument's own `::` isn't mistaken for a namespace boundary).
    # Deliberately *eager*: a spurious match makes the two sides' sets
    # differ, which keeps the finding -- the only safe direction here.
    wanted = {name, *_namespace_suffix_spellings(name)}

    def _owns(fn: Function) -> bool:
        owner = owner_class_of(fn)
        if not owner:
            return False
        return bool(wanted & {owner, *_namespace_suffix_spellings(owner)})

    return {
        mangled
        for mangled, fn in funcs.items()
        if getattr(fn, "is_virtual", False) and _owns(fn)
    }


def _owned_virtual_signatures_for_record(
    qualified: str, funcs: Mapping[str, Function]
) -> set[str]:
    """The mangled names of *funcs*' virtual member functions owned by
    *qualified* -- an exact identity, matched by the bare-leaf suffix
    matching ``_owned_virtual_signatures`` above uses.

    That function's eager namespace-suffix matching is safe for its own
    caller (``_vtable_transition_is_evidenced``): over-inclusion just makes
    the two sides' sets differ, which reads as "real evidence, keep the
    finding" -- the safe direction for a suppression. It is unsafe here: our
    caller, ``_vtable_transition_rests_on_unresolved_evidence``, treats
    "sets differ" as real evidence and *declines to correlate* -- so an
    unrelated same-leaf-name record in a different namespace (e.g. an
    unrelated ``ns2::Foo::g`` while scoping ``ns1::Foo``'s own evidence gap)
    silently makes a genuine ``ns1::Foo`` pair's sets look different and the
    pair never receives ``correlated_change_kind`` (Codex review, fresh
    evidence).

    ``owner_class_of`` always returns a *fully* scope-qualified owner when it
    returns anything at all -- its display-name branch only fires on an
    already-qualified name, and its mangled-name fallback reconstructs the
    complete nested-name chain, never a partial one. So an exact string
    comparison against *qualified* (also fully qualified -- see the caller)
    is precise here, unlike the bare-name comparison an earlier revision
    tried and reverted (see ``_owned_virtual_signatures``'s own docstring).

    Takes the identity as a plain string, computed *once* by the caller from
    both ``RecordType``s of an already-matched pair, rather than deriving it
    separately per side from each ``RecordType``'s own ``qualified_name``:
    a legacy stored snapshot can carry ``qualified_name=None`` for a record
    the *other* side (or ``TypeMap``'s own ambiguity-safe bare-name
    fallback) already knows is the same namespaced type as a fresher
    snapshot's fully-qualified one -- comparing each side against its own,
    independently-derived identity would then compare "Foo" (old) against
    "ns::Foo" (new), permanently mismatching every owner and manufacturing a
    spurious "independently evidenced" verdict for a comparison that has
    nothing to do with either side's actual virtual surface (Codex review,
    fresh evidence).

    Declining a match is always safe for this caller: it only feeds a
    cosmetic cross-reference annotation, never a finding's presence or
    severity. A template specialization whose owner reconstruction falls
    back to the raw Itanium argument encoding (``BoxIiE``) rather than the
    spelled ``qualified_name`` (``Box<int>``) simply produces no match here
    -- the same outcome eager suffix matching already had for that case
    (neither spelling shares a namespace-suffix with the other), so this is
    not a new gap.
    """
    from .diff_cxx_rules import owner_class_of

    return {
        mangled
        for mangled, fn in funcs.items()
        if getattr(fn, "is_virtual", False) and owner_class_of(fn) == qualified
    }


def _diff_type_vtable(
    name: str,
    t_old: RecordType,
    t_new: RecordType,
    old_funcs: dict[str, Function],
    new_funcs: dict[str, Function],
    old_types: Mapping[str, RecordType],
    new_types: Mapping[str, RecordType],
    *,
    vtable_facts_reliable: bool = True,
) -> list[Change]:
    old_vtable = resolved_fact_value(t_old.vtable_fact, [])
    new_vtable = resolved_fact_value(t_new.vtable_fact, [])
    if old_vtable == new_vtable:
        return []
    if not vtable_facts_reliable:
        # Either side is a persisted, pre-v21 direct-clang snapshot whose
        # vtable was unconditionally vtable=[] for EVERY record -- real but
        # WRONG data for an already-polymorphic class, not merely absent
        # (AbiSnapshot.clang_vtable_facts_reliable's own docstring). Every
        # differing-vtable pair reaching this point on such a comparison is
        # capture-tool noise, not a genuine change, so decline exactly like
        # the unevidenced-transition guard below -- see
        # _vtable_transition_is_evidenced for the same discipline applied to
        # a different (genuinely ambiguous, rather than known-wrong) cause.
        return []
    if not _vtable_transition_is_evidenced(name, t_old, t_new, old_funcs, new_funcs):
        return []
    # Same slot count/order, every differing slot a same-signature override
    # reusing its base's slot (case185) -> no real layout change, just a
    # slot's mangled owner renaming base to derived; func_added already
    # covers the new symbol. Mirrors virtual_method_addition()'s exemption
    # (own-owner-descends-from-old-owner guard included, so an unrelated
    # same-signature virtual can't false-suppress a genuine replacement).
    if len(old_vtable) == len(new_vtable) and all(
        vtable_slot_is_override_reuse(
            old_entry, new_entry, old_funcs, new_funcs, old_types, new_types
        )
        for old_entry, new_entry in zip(old_vtable, new_vtable)
    ):
        return []
    description = (
        f"vtable reordered: {name}"
        if Counter(old_vtable) == Counter(new_vtable)
        else f"vtable changed: {name}"
    )
    change = make_change(
        ChangeKind.TYPE_VTABLE_CHANGED,
        symbol=name,
        description=description,
        old_value=", ".join(old_vtable),
        new_value=", ".join(new_vtable),
        entity_id=t_old.entity_id or t_new.entity_id,
    )
    # Tag (never demote) when this finding rests on the identical evidence
    # gap LAYOUT_UNVERIFIABLE reports for the same (exact, already
    # type-matched) RecordType pair.
    #
    # An earlier revision of this fix set ``effective_verdict =
    # COMPATIBLE_WITH_RISK`` here instead -- wrong, and wrong for a reason
    # this file's own AGENTS.md "Findings emitted from absent evidence"
    # entry already states for the sibling suppression this mirrors: "an
    # unknown size on either side corroborates nothing but also refutes
    # nothing, so the finding is kept... not a fallback for missing
    # information." The genuinely ambiguous case this branch identifies --
    # old vtable populated, new vtable empty/differing, new-side evidence
    # missing -- is indistinguishable from a real removal of the class's
    # last virtual method whose only definition happens to live in a TU the
    # new side's debug info doesn't cover (Codex review, fresh evidence: a
    # pure-virtual method absent from both function maps reaches exactly
    # this branch). Demoting BREAKING to RISK there risks hiding a real ABI
    # break behind a capture gap, the opposite of this codebase's documented
    # default (false-negative-avoidance over false-positive-avoidance).
    #
    # So the finding stays BREAKING here, full stop. Instead this only
    # records ``qualified_name`` + a dedicated internal correlation marker
    # (``vtable_covers_unverifiable_layout_gap`` -- deliberately NOT
    # ``modulation_reason``/``modulation_rule``, which are a public,
    # report-facing audit trail for an actual verdict override that did not
    # happen here; Codex review) so a post-processing step
    # (``post_processing.AnnotateLayoutUnverifiableCoveredByVtableChanged``)
    # can recognize that THIS type's LAYOUT_UNVERIFIABLE finding shares the
    # exact same evidence gap and cross-reference it via
    # ``Change.correlated_change_kind`` -- rather than reporting the same
    # gap twice at two different (and, before this fix, contradictory-
    # looking) severities with no link between them. Both findings stay
    # fully reported; nothing is ever removed from ``changes`` for this
    # reason (an earlier fold-based design was reverted -- see AGENTS.md's
    # "Findings emitted from absent evidence" entry for why a compare()-time
    # removal here can never be correct for every downstream consumer).
    if _vtable_transition_rests_on_unresolved_evidence(
        t_old, t_new, old_funcs, new_funcs
    ) and _layout_evidence_is_unverifiable(
        t_old, t_new, vtable_facts_reliable=vtable_facts_reliable
    ):
        change.qualified_name = t_new.qualified_name or t_new.name
        change.vtable_covers_unverifiable_layout_gap = True
    return [change]
