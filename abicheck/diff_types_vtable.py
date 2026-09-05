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
only symbol this module exports). ``_vtable_transition_is_evidenced``/
``_vtable_transition_rests_on_unresolved_evidence``/
``_layout_evidence_is_unverifiable`` are private to this cluster and only
ever called from within it (``_vtable_transition_is_evidenced`` is now a
thin wrapper over ``compare/vtable_evidence.py``'s shared predicate, its
own ``_owned_virtual_signatures`` helper moved there with it -- see the
"ADR-063 Track 2, 5B closure" note below).
``_virtual_signatures_by_owner`` is the one exception (Codex review, fresh
evidence): ``diff_vtable_layout._is_polymorphic`` also imports it directly
as a sixth positive-evidence path (a retained ``Function.is_virtual``
proves polymorphism independent of ``RecordType.vtable``, the same
"different projection of the same debug info" reasoning this cluster
already relies on) rather than reimplementing its own copy of the
owner-matching logic -- built on the same *exact*-match identity
``_owned_virtual_signatures_for_record`` uses (a single-owner query, still
private to this cluster, that ``_virtual_signatures_by_owner`` now shares
its ``owner_class_of`` matching with), not the moved
``compare.vtable_evidence``'s own eager namespace-suffix matching: that new
caller uses a match as an
unconditional affirmative ``True``, the opposite safety direction from
this cluster's own suppression-oriented use, where over-inclusion only
ever widens "keep the finding," never fabricates one (Codex review, second
round, caught the first attempt reusing the eager variant here). Grouped
by owner once instead of scanning the whole function map per query (Codex
review, third round -- that new caller can query many distinct owners
against the same mapping while walking an inheritance graph). This does
not reintroduce the import-cycle constraint above: ``diff_vtable_layout.
py`` imports nothing from ``diff_types`` itself, only from this leaf
module.

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
``FAILED``).

**ADR-063 Track 2, 5B closure (this revision).** The predicate itself --
``_vtable_transition_is_evidenced`` and its ``_owned_virtual_signatures``
helper -- moved to ``compare/vtable_evidence.py`` as
``vtable_transition_is_evidenced``, a genuine leaf below both this module
and ``diff_cxx_rules.py`` (it takes ``owner_class_of``/
``namespace_suffix_spellings`` as injected callables instead of importing
either, so it depends on ``model`` only). The two thin wrappers below keep
this module's own public names and call signature unchanged for every
existing caller (including ``tests/test_vtable_evidence_guard.py``, which
imports ``_vtable_transition_is_evidenced`` directly). ``diff_cxx_rules.
virtual_method_addition`` now imports the same shared predicate and calls
it before deferring, rather than assuming -- on prose alone -- that this
cluster will fire whenever the raw vtable arrays differ. See
``virtual_method_addition``'s own docstring for what changed there. This
closes the gap the previous revision of this docstring recorded: an
unevidenced difference (same owned-virtual-function set, same size, same
virtual-base list on both sides) no longer leaves *both* detectors silently
declining -- ``virtual_method_addition`` now falls through to its own
signature-based override check instead.

**ADR-063 Track 4, 5B final closure (this revision): re-examined across
three rounds, ending in a declined fix -- not the landed one an earlier
revision of this docstring described.** The consolidation above removed
the *import-cycle* constraint that previously blocked
``virtual_method_addition`` from consulting this cluster's real verdict --
and that constraint, now resolved, was never what stood between this
cluster and a direct ``FactStatus`` pre-check in the first place.
Re-checked from scratch rather than assumed: the consolidation changes
nothing about what DWARF's own per-TU extraction can observe, so the
false positive this guard exists to suppress -- ``Fact.present([])``,
genuinely ``PRESENT``, for a class whose virtual methods live in a
translation unit only the *other* side's debug info covers -- remains
exactly as unable to be distinguished from a genuinely non-polymorphic
class via ``vtable_fact.status`` alone as it was before this module
existed. That part of the investigation stands across all three rounds:
this guard's DWARF-gap heuristic is unchanged, and still does not consult
``vtable_fact.status`` for that reason.

The narrower question -- gating specifically on ``vtable_fact.status``
being ``NOT_COLLECTED``/``FAILED``/otherwise-not-``is_present`` (as
opposed to conflating it with a confirmed-``PRESENT([])`` empty read) --
went through three rounds on this PR, each correcting the previous one:

1. **First round: declined**, reasoning that ``resolved_fact_value``'s
   existing collapse already excludes a non-present side from the "both
   sides captured something" branch (making a direct check redundant
   there), and that using the status to short-circuit the two fallback
   evidence streams (owned virtual functions, ``size_bits``/
   ``virtual_bases_fact``) would be unsafe if either stream ever
   legitimately fires for a real, non-present-status side.
2. **Second round: landed, reversing the first.** Codex review supplied a
   fact the first round was missing: ``pdb_model.py``'s
   ``_record_from_layout`` -- the real PDB extractor, not a hand-built
   test fixture -- never sets ``vtable``/``vtable_fact`` at all for any
   record, which the ``RecordType.__post_init__`` bridge (``model/fact.py``'s
   ``bridge_legacy_and_fact``) resolves to ``Fact.not_collected()``
   unconditionally, for *every* PDB-derived record, independent of
   ``AbiSnapshot.clang_vtable_facts_reliable``. A cross-backend comparison
   against a PDB side following either fallback stream on that status
   produced a *reachable, confirmed* fabricated ``TYPE_VTABLE_CHANGED``
   (an apparent vtable removal) from an unrelated size delta or a
   mangling-scheme artifact. Reasoning that DWARF and both header-AST
   backends always construct ``vtable_fact`` as ``Fact.present(...)``/
   ``Fact.partial(...)``, never omitted -- confirmed by inspecting their
   real ``RecordType(...)`` construction sites -- a decline gated on
   ``not is_present`` looked safe: those backends never carry the status
   that triggers it. ``compare.vtable_evidence.vtable_transition_is_
   evidenced`` landed exactly that decline.
3. **Third round: reverted, and declined again -- this time for the right
   reason.** The "DWARF/header-AST backends never produce this status"
   reasoning was correct but incomplete: it checked every *real extractor*
   construction site, but ``vtable`` is also a public, positional field on
   the public ``RecordType`` dataclass, and *omitting* it at construction
   -- not just PDB's own extractor never setting it -- resolves to
   ``Fact.not_collected()`` via the identical ``bridge_legacy_and_fact``
   bridge. That omission is exactly how a large fraction of this
   codebase's own hand-constructed test fixtures (and any external
   typed-API caller using the same public constructor) spell "this class
   has no vtable" for an ordinary non-polymorphic class -- a shape with
   nothing to do with PDB, and one the "not is_present" decline could not
   tell apart from PDB's own structural non-evidence. Landing it silently
   regressed a previously-passing, real scenario:
   ``tests/test_abicc_scenario_parity.py::
   TestLeafClassVirtualMethodAdditions::test_virtual_added_to_leaf_class``
   (a leaf class's old side omits ``vtable=`` entirely, meaning "no
   virtuals"; the new side gains one via ``Function.is_virtual`` evidence
   -- exactly the owned-virtual-function fallback stream the decline
   short-circuited). Caught by running the full test suite before
   declaring the fix complete, not by review alone. Reverted in full: this
   function's heuristic is unchanged from before this closure began.

This closed ADR-063 Phase 5B's own removal gate for the ``vtable`` field
family as a formal, investigated decline -- the same disposition 2B's
`entity:` alias promotion and 6B's own undone cohort items received, per
``docs/contribute/plans/one-semantic-pipeline.md``'s 5B section and
``docs/_meta/one-semantic-pipeline-status.yaml``'s ``facts`` concept --
**not** left ambiguous between the two outcomes, and not silently reverted
without a trace: the PDB fabrication round 2 found was real and
reachable, recorded here and in the plan rather than only in that PR's
own history.

**T9 closure (duplication-and-convergence-assessment.md Phase 6 item 4):
the PDB fabrication above is now closed.** The 2026-09-04 note above
diagnosed the blocker precisely: ``vtable_fact.status`` alone could not
tell "this whole backend never captures vtable data" apart from "this one
record's constructor simply omitted the field," because both PDB's own
non-evidence and a hand-built/typed-API omission resolved to the
identical ``NOT_COLLECTED`` status. The fix is not a snapshot-wide
reliability flag (a `clang_vtable_facts_reliable`-shaped side channel) --
it is using the status ``FactStatus`` already reserves for exactly PDB's
situation, explicitly, at the one place that knows it: ``pdb_model.py``'s
``_record_from_layout`` now constructs every PDB record's
``vtable_fact``/``vptr_offset_bits_fact`` as ``Fact.unsupported(...,
producer="pdb")`` rather than omitting the field. `compare/vtable_
evidence.py`'s `vtable_transition_is_evidenced` (this module's
``_vtable_transition_is_evidenced`` wraps it unchanged) now declines
outright — before consulting either fallback evidence stream — whenever
either side's `vtable_fact.status is FactStatus.UNSUPPORTED`. See that
module's own "T9 closure" docstring note for the full account, including
why gating on `UNSUPPORTED` specifically (not the broader `not
is_present` round 2 tried) does not reopen the leaf-class regression round
3 protects: a typed-API omission never resolves to `UNSUPPORTED`, only to
`NOT_COLLECTED`, and this gate does not touch `NOT_COLLECTED` at all.

This also closes the second, distinct fabrication path a prior revision
of this docstring recorded as deliberately out of scope: the
owned-virtual-function fallback stream (``pe_metadata.py``/
``pdb_metadata.py`` never set ``Function.is_virtual`` explicitly, so it
reads its dataclass default ``False`` for every PE/PDB-sourced function —
the identical "default reads as confirmed absence" ambiguity, one
projection over). That stream is only ever consulted *after* the new
``UNSUPPORTED`` gate above, so a PDB-derived side's own
``vtable_fact=Fact.unsupported(...)`` declines the whole predicate before
either fallback stream — including the owned-virtual-function one — is
ever reached; no separate fix to ``Function.is_virtual`` was needed to
close this half.

**What T9 does not close, and remains genuinely open:** the DWARF
per-translation-unit completeness gap this cluster's own ``vtable_
transition_is_evidenced`` docstring already names — a class whose virtual
methods live in a TU only the *other* side's debug info covers still
reads ``Fact.present([])``, genuinely ``PRESENT``, on the losing side.
That is not a producer-*capability* gap ``producer``/``UNSUPPORTED`` can
express (DWARF genuinely can capture this family; the gap is per-TU
*scope*, not per-producer capability) — it is exactly the
observed-vs-inferred / positive-observation-vs-completeness distinction
the wider T9 tracking item (`docs/contribute/plans/duplication-and-
convergence-assessment.md` Phase 6 item 4) still leaves unimplemented,
and closing it needs either a per-TU coverage signal this model does not
yet carry or artifact-level evidence (`_ZTV` presence, a per-finding
provider record) this type-level detector does not receive — not a
further reading of the fields already here. Recorded rather than left
implicit, per this file's own "say so explicitly" convention.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping

from .checker_policy import ChangeKind
from .checker_types import Change
from .compare.vtable_evidence import vtable_transition_is_evidenced
from .diff_cxx_rules import owner_class_of, vtable_slot_is_override_reuse
from .diff_helpers import make_change
from .model import Function, RecordType, resolved_fact_value
from .type_reachability_spelling import _namespace_suffix_spellings


def _owned_virtual_signatures(name: str, funcs: Mapping[str, Function]) -> set[str]:
    """The mangled names of *name*'s own virtual member functions.

    A thin wrapper over ``compare.vtable_evidence``'s private helper of the
    same name (ADR-063 Track 2, 5B closure moved the implementation there
    alongside ``vtable_transition_is_evidenced``, its one caller) -- kept
    here under this exact name and signature purely for back-compat:
    ``diff_types.py`` re-exports this name by value (``from .diff_types_vtable
    import _owned_virtual_signatures as _owned_virtual_signatures``) so any
    call site still resolving ``abicheck.diff_types._owned_virtual_signatures``
    keeps working. Not called from within this module any more -- see
    ``_vtable_transition_is_evidenced`` below, which calls the shared
    predicate directly instead of this helper.
    """
    from .compare.vtable_evidence import _owned_virtual_signatures as _shared

    return _shared(
        name,
        funcs,
        owner_class_of=owner_class_of,
        namespace_suffix_spellings=_namespace_suffix_spellings,
    )


def _vtable_transition_is_evidenced(
    name: str,
    t_old: RecordType,
    t_new: RecordType,
    old_funcs: Mapping[str, Function],
    new_funcs: Mapping[str, Function],
) -> bool:
    """Whether an *empty↔non-empty* vtable difference rests on real evidence.

    A thin wrapper over the shared, module-independent predicate in
    ``compare/vtable_evidence.py`` (ADR-063 Track 2, 5B closure) -- kept here,
    under this exact name and signature, so every existing caller in this
    file and ``tests/test_vtable_evidence_guard.py`` is unaffected. See that
    module's ``vtable_transition_is_evidenced`` for the full rationale and
    false-positive history; it is unchanged for this module's own callers.
    """
    return vtable_transition_is_evidenced(
        name,
        t_old,
        t_new,
        old_funcs,
        new_funcs,
        owner_class_of=owner_class_of,
        namespace_suffix_spellings=_namespace_suffix_spellings,
    )


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


def _owned_virtual_signatures_for_record(
    qualified: str, funcs: Mapping[str, Function]
) -> set[str]:
    """The mangled names of *funcs*' virtual member functions owned by
    *qualified* -- an exact identity, matched by the bare-leaf suffix
    matching ``compare.vtable_evidence``'s own ``_owned_virtual_signatures``
    helper uses (moved there from this module -- see this file's own module
    docstring, "ADR-063 Track 2, 5B closure").

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
    tried and reverted (see ``compare.vtable_evidence._owned_virtual_
    signatures``'s own docstring).

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


def _virtual_signatures_by_owner(
    funcs: Mapping[str, Function],
) -> dict[str, set[str]]:
    """Every virtual member function in *funcs*, grouped by exact owning-
    class identity (same ``owner_class_of`` exact match
    ``_owned_virtual_signatures_for_record`` uses) -- computed once instead
    of that function's own per-query full scan.

    Built for ``diff_vtable_layout._is_polymorphic``'s retained-virtual-
    ``Function`` evidence path (Codex review, fresh evidence): a caller
    that queries *many* distinct owners against the same ``funcs`` mapping
    (walking a whole inheritance graph) turns an O(records) scan into
    O(records × functions) if it calls ``_owned_virtual_signatures_for_
    record`` fresh each time. One pass here, then each caller query is a
    plain dict lookup.
    """
    from .diff_cxx_rules import owner_class_of

    index: dict[str, set[str]] = {}
    for mangled, fn in funcs.items():
        if not getattr(fn, "is_virtual", False):
            continue
        owner = owner_class_of(fn)
        if owner is None:
            continue
        index.setdefault(owner, set()).add(mangled)
    return index


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
        #
        # `old_value`/`new_value` below are built from `resolved_fact_value
        # (...,  [])`, which on its own cannot distinguish "confirmed empty"
        # from "not collected" -- so a NOT_COLLECTED status reaching that
        # construction renders as an empty vtable rather than an unknown
        # one. This early return catches that for the
        # clang-legacy-unreliable case specifically. It is NOT caught in
        # general: a PDB-derived side's `vtable_fact` is always
        # NOT_COLLECTED (independent of `clang_vtable_facts_reliable`,
        # which PDB never touches), and `_vtable_transition_is_evidenced`
        # does not gate on `vtable_fact.status` at all -- a fix that added
        # exactly that gate was attempted, landed, and reverted on this
        # same PR (ADR-063 Track 4, 5B final closure -- see this module's
        # own docstring for the full three-round account) after it
        # regressed real detection coverage for an unrelated, far more
        # common shape (a hand-constructed/typed-API `RecordType` that
        # simply omits `vtable=`). The PDB fabrication this early return's
        # own reasoning describes therefore remains real, reachable, and
        # open for a comparison this function's `vtable_facts_reliable`
        # parameter does not cover.
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
