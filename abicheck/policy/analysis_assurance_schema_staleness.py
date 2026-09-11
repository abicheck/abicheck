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

"""Whether either side of a comparison carries a ``*_facts_reliable`` flag
``policy.analysis_assurance_degraded_facts.degraded_reliability_facts``
marks stale.

Split out of ``analysis_assurance.py`` (which sits at this repo's
``architecture/debt.yaml`` no-growth baseline) rather than added there, and
placed under the real ``policy`` package -- not a new flat-root legacy
sibling -- per this repo's "valid extraction" rule (``abicheck/AGENTS.md``
"Working with legacy large modules": name a responsibility and its
destination package, add no new legacy/debt-ledger entry). This module
depends only on ``model.AbiSnapshot`` plus its own sibling ``policy.
analysis_assurance_degraded_facts`` (itself ``model.snapshot_reliability``
plus the detector-consultation table -- moved into ``policy`` in round 7,
see that module's own docstring), within ``policy``'s own ``may_import``
(Codex review, PR #1209).

**The gap this closes:** loading a snapshot whose own ``schema_version``
predates this abicheck's, or one re-saved since without ever being
regenerated, already produced a load-time ``UserWarning`` naming the
degraded fact (``serialization.snapshot_from_dict``) -- but that warning is
stderr-only, invisible to any programmatic consumer of the JSON report, and
``analysis_assurance``'s own completeness rollup had no signal for it at
all: a run with one or more degraded facts still read
``run_outcome.assurance.status == "complete"``. This module is that
missing signal, computed from the exact same table the load-time warning
uses (via ``degraded_reliability_facts``) so the two can never
independently drift on what counts as "degraded".

**Known, accepted limitation (Codex review, PR #1209, narrowed round 11):**
``clang_field_initializer_facts_reliable``'s True downstream cost is
per-declaration and value-shape-dependent
(``diff_default_value_reliability._fingerprint_comparison_unreliable``
only actually suppresses a comparison when the two sides' fingerprint
*generations* differ AND the specific field's own value is fingerprint-
shaped -- two same-vintage legacy snapshots compare their fingerprints
just fine). Modeling THAT accurately would mean walking every field's own
resolved value/producer here, which conflicts with this module's (and the
pre-existing load-time warning's) deliberate "rollup over already-computed
snapshot-level fields, never a new per-declaration probe" contract -- see
``analysis_assurance.py``'s own module docstring. Left conservative (a
False flag always taints within a pair that clears the coarser producer-
match gate below) rather than attempting an incomplete pair-aware model:
the failure direction is safe (a spurious ``"degraded"`` under-claims
confidence; it can never fabricate a ``"complete"`` claim the P1 bug this
module exists to fix was about). The COARSER, whole-snapshot-level half of
this flag's real gate -- a genuine cross-backend producer MISMATCH, which
``diff_symbols._diff_param_defaults``/``diff_types_field_facts._diff_
field_default_initializer`` both decline on regardless of this flag's own
value -- IS pair-gated as of round 11 (see :data:`_PAIR_SAME_PRODUCER_AS_
SNAP_GATED_FLAGS`); only the deeper, per-value-shape nuance above remains
unmodeled.

**Second known, accepted limitation (Codex review, PR #1209 round 4, revised
round 9):** this module reads *whatever* ``old``/``new`` it is given -- it
cannot tell whether either snapshot was already depth-projected (``policy.
depth_projection``, e.g. a ``--depth binary`` comparison) before reaching
here, except where a projected snapshot's own post-projection SHAPE already
answers the question directly (see :data:`_DEPTH_PROJECTION_PARAMS_CLEARED_
FLAGS` below -- ``param_kind_facts_reliable`` is fixed as of round 9; the
round-4 reply's claim that it "stays fully relevant... intact, just demoted
to ELF_ONLY" was WRONG, disproven by fresh evidence: a non-DWARF-sourced
projection clears ``Function.params`` to ``[]`` entirely, not merely
demotes visibility, and ``diff_symbols._is_stripped_symbols_only`` -- the
real detector's own gate -- fires on exactly that shape). What remains
unfixed: ``header_cv_facts_reliable``/``clang_vtable_facts_reliable``'s
type-level consumers (``diff_types.py``/``diff_vtable_layout.py`` and
siblings) go silent once projection clears ``types`` (the non-DWARF-sourced
case) but stay live when DWARF-sourced (kept wholesale, origin reset to
``UNKNOWN``) -- and unlike the param-kind case, this module has not yet
verified that EVERY real consumer of these two flags is type-map-gated the
same way (``diff_layout.py``'s vtable-offset detector in particular has not
been read closely enough to rule out a raw-DWARF-layout path independent of
``types``). Given the "attempted twice, reverted twice" discipline
`AGENTS.md`'s "Primitive-level property tests" section names for a
heuristic that keeps finding one more counterexample, and this module's own
first known limitation just above already accepting exactly this trade-off
for a harder case, these two flags are left conservative rather than risk a
third confident-but-wrong claim: over-reporting ``"degraded"`` after a depth
projection is the safe direction, never a fabricated ``"complete"``.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from .analysis_assurance_degraded_facts import degraded_reliability_facts

if TYPE_CHECKING:
    from ..model import AbiSnapshot

__all__ = ["schema_staleness_status"]

#: The two flags whose one real consumer (``diff_symbols._diff_param_va_
#: list``/``_diff_var_access``) gates on BOTH sides sharing one exact
#: ``ast_producer`` AND both being header-*confirmed* (``_both_header_
#: aware`` -- non-inferred ``from_headers`` on each side), not merely this
#: side's own. ``degraded_reliability_facts(snap)`` already requires *this*
#: side to be the named producer AND header-confirmed before listing
#: either flag (a real, single-snapshot narrowing), but it cannot see the
#: *other* side at all, so a pair where *this* side qualifies but *other*
#: doesn't -- wrong producer (a degraded, confirmed-header "clang" old side
#: paired with a "castxml" new side), or the right producer but only
#: *inferred* header awareness (``from_headers_inferred=True``) -- would
#: otherwise still read the flag as consulted even though the detector's
#: own both-sides gate means it was never reached at all for this pair
#: (Codex review, PR #1209, rounds 2 and 5). Fixed here, in the pair-aware
#: assurance layer, rather than in ``model.snapshot_reliability`` -- that
#: module's own scoped contract (``model/AGENTS.md``) is single-snapshot
#: fact shapes, never a detector's pairing algorithm.
_PAIR_PRODUCER_GATED_FLAGS: dict[str, str] = {
    "clang_va_list_facts_reliable": "clang",
    "castxml_var_access_facts_reliable": "castxml",
}

#: The one flag whose real consumer requires BOTH sides confirmed
#: (non-inferred) header-aware -- ``_both_header_aware`` -- but, unlike
#: :data:`_PAIR_PRODUCER_GATED_FLAGS`, places no further requirement on the
#: *other* side's producer (Codex review, PR #1209 round 7, fresh evidence):
#: ``diff_symbols._diff_param_restrict`` exits at ``_both_header_aware``
#: before ever reading ``clang_restrict_facts_reliable``, and (unlike
#: ``TypeField.default``/``Param.default``) a restrict-qualification bool is
#: directly cross-comparable once both backends populate it (v22+), so no
#: producer match is needed. ``clang_deprecation_facts_reliable`` moved OUT
#: of this set in round 8 -- see :data:`_PAIR_KNOWN_DEPRECATION_PRODUCER_
#: GATED_FLAGS` below. ``clang_field_initializer_facts_reliable`` moved OUT
#: in round 11 -- see :data:`_PAIR_SAME_PRODUCER_AS_SNAP_GATED_FLAGS` below,
#: its real consumers need a producer MATCH, not just header confirmation.
_PAIR_HEADER_ONLY_GATED_FLAGS: frozenset[str] = frozenset(
    {
        "clang_restrict_facts_reliable",
    }
)

#: ``clang_deprecation_facts_reliable`` (Codex review, PR #1209 round 8,
#: revised round 10 -- see :func:`_other_side_supports_known_producer_
#: comparison`'s own docstring for the full corrected account).
#: ``diff_symbols._diff_func_deprecated`` calls ``fact_provenance.
#: fact_producer`` independently on BOTH sides and skips the pair entirely
#: if either call returns ``None``, which happens whenever *that* side
#: isn't confirmed header-aware -- the SAME single condition
#: :data:`_PAIR_HEADER_ONLY_GATED_FLAGS` already checks for its two
#: members. This flag needed its own category rather than joining that set
#: outright for exactly one further nuance ``fact_producer``'s hybrid
#: branch introduces: a "hybrid" ``other`` needs an actually-recorded
#: per-declaration provenance entry too, not just the producer label (see
#: :func:`_other_side_has_hybrid_deprecation_provenance`). An earlier
#: revision of this comment (round 8) also excluded an unknown or
#: itself-degraded-clang ``other`` producer -- reverted in round 10 as a
#: real bug, not a refinement: both are themselves schema-vintage-degraded
#: facts, not a permanent incompatibility, so excluding on that basis
#: silently turned "both sides are stale" into a false "clean".
_PAIR_KNOWN_DEPRECATION_PRODUCER_GATED_FLAGS: frozenset[str] = frozenset(
    {"clang_deprecation_facts_reliable"}
)

#: ``clang_field_initializer_facts_reliable`` (Codex review, PR #1209 round
#: 11, fresh evidence). Unlike ``clang_deprecation_facts_reliable``
#: (values ARE cross-comparable once both producers are known, any
#: combination), ``TypeField.default``/``Param.default``'s VALUE
#: REPRESENTATIONS are NOT cross-comparable across backends (castxml keeps
#: the verbatim source expression; clang falls back to a literal/structural
#: fingerprint) -- exactly the same shape :data:`_PAIR_PRODUCER_GATED_FLAGS`
#: already guards for va_list/var_access. ``diff_symbols._diff_param_
#: defaults`` skips a function pair outright when both sides' PER-
#: DECLARATION producers are positively known and DIFFER (unrelated to this
#: flag's own value); ``diff_types_field_facts._diff_field_default_
#: initializer`` gates per-field on ``fact_same_producer_qualified``, the
#: identical same-known-producer requirement. So a degraded, confirmed-
#: header "clang" side paired with a confirmed "castxml" side means these
#: detectors decline the comparison regardless of what this flag says --
#: a REAL, permanent incompatibility (unless a frontend choice changes),
#: not a regeneratable gap. Unlike :data:`_PAIR_PRODUCER_GATED_FLAGS`'s two
#: fixed-string members, this flag has no single required producer (it can
#: be False for a "clang" OR a legacy unset-producer snapshot) -- the
#: requirement is that *snap*'s own producer, whatever it is, MATCHES
#: *other*'s, so :func:`_other_side_matches_snap_producer` takes *snap* as
#: well as *other*. An unknown producer on EITHER side is deliberately NOT
#: treated as a permanent mismatch (round-10 principle,
#: ``_other_side_supports_known_producer_comparison``'s own docstring): it
#: is itself just another regeneratable, schema-vintage gap. A "hybrid"
#: producer on EITHER side is ALSO deliberately never excluded on its own
#: (round 12 -- see :func:`_other_side_matches_snap_producer`'s own
#: docstring): the real gate resolves producer PER DECLARATION for a
#: hybrid snapshot, so a whole-snapshot label mismatch against "hybrid"
#: does not prove the actually-matched declaration disagrees too.
_PAIR_SAME_PRODUCER_AS_SNAP_GATED_FLAGS: frozenset[str] = frozenset(
    {"clang_field_initializer_facts_reliable"}
)


def _other_side_is_header_confirmed(other: AbiSnapshot) -> bool:
    """Whether *other* alone clears ``_both_header_aware``'s own half of the
    gate -- confirmed (non-inferred) header awareness, no producer
    requirement.
    """
    return other.from_headers and not other.from_headers_inferred


def _other_side_confirms_pair_gate(other: AbiSnapshot, producer: str) -> bool:
    """Whether *other* alone would satisfy ``_diff_param_va_list``'s/
    ``_diff_var_access``'s own ``_both_header_aware`` + exact-producer gate
    -- confirmed (non-inferred) header awareness AND the exact matching
    *producer*, mirroring ``diff_symbols.py``'s own two checks.
    """
    return _other_side_is_header_confirmed(other) and other.ast_producer == producer


def _other_side_has_hybrid_deprecation_provenance(other: AbiSnapshot) -> bool:
    """Whether *other*'s own ``fact_provenance`` dict actually recorded a
    producer for AT LEAST ONE ``deprecated``/``is_scoped`` key (Codex
    review, PR #1209 round 9, fresh evidence).

    ``fact_producer``'s ``ast_producer == "hybrid"`` branch is ``snap.
    fact_provenance.get(key)`` -- a per-declaration lookup, not a whole-
    snapshot guarantee the way ``"castxml"``/``"clang"`` are (every fact on
    a single-backend snapshot came from that one backend unconditionally).
    A hybrid merge only stamps provenance for a declaration it actually
    matched; treating ``ast_producer == "hybrid"`` alone as "known producer"
    (the prior round's shape) could taint the status even when NO
    declaration's fact_provenance carries a resolvable ``:deprecated``/
    ``:is_scoped`` entry at all, in which case the detector never actually
    reaches a single comparison.

    Still a whole-snapshot rollup, not a per-declaration VALUE probe (unlike
    ``clang_field_initializer_facts_reliable``'s documented, deliberately
    unmodeled per-declaration/value-shape limitation): ``fact_provenance``
    is itself an already-materialized snapshot-level ``dict[str, str]``
    field (``model/snapshot.py``), so checking whether it contains ANY key
    of the right shape is an existence scan over data already on the
    object, not a new walk of declarations/types to resolve a value.

    **Third known, accepted limitation (Codex review, PR #1209 round 10):**
    this existence scan is whole-snapshot, not per-DECLARATION -- an
    unrelated ``:deprecated``/``:is_scoped`` provenance entry for some OTHER
    function/enum elsewhere in *other* makes this return ``True`` even when
    every declaration actually matched against the stale side lacks its own
    key, so the detector still declines those specific pairs while this
    reports the pair as merely "degraded" rather than excluding it. Fully
    precise would mean walking matched declaration pairs between *snap* and
    *other* to check each one's own key -- exactly the per-declaration probe
    this module's "rollup over already-computed snapshot-level fields"
    contract (see the module docstring's first known limitation) declines
    to attempt. This is the safe direction (over-reporting ``"degraded"``,
    never fabricating ``"complete"``), unlike the round-8/10
    unknown-and-degraded-producer bug this same round's evidence also
    disproved (see :func:`_other_side_supports_known_producer_comparison`'s
    own docstring) -- that one silently hid a real gap; this one only ever
    over-taints. Left as a second, deliberately accepted per-declaration
    limitation on this exact flag rather than a third revision of this
    heuristic.
    """
    return any(
        key.endswith(":deprecated") or key.endswith(":is_scoped")
        for key in other.fact_provenance
    )


def _other_side_supports_known_producer_comparison(other: AbiSnapshot) -> bool:
    """Whether *other* being confirmed header-aware is enough to say the
    deprecation detector COULD still reach a real comparison for this pair
    -- confirmed header awareness, and -- for a "hybrid" producer
    specifically -- an actually-recorded per-declaration provenance entry
    (see :func:`_other_side_has_hybrid_deprecation_provenance`).

    **Round 10 correction (Codex review, PR #1209, fresh evidence):** an
    earlier revision of this function ALSO excluded *other* for having an
    unknown (``None``) ``ast_producer`` or for being itself an unreliable
    confirmed-header "clang" producer for this exact fact family --
    reasoning that ``fact_producer(other, ...)`` really does resolve
    ``None`` in both cases, so the detector "structurally can't run for
    either side's sake." That reasoning was WRONG: unlike a genuine
    cross-backend PRODUCER MISMATCH (:data:`_PAIR_PRODUCER_GATED_FLAGS`'s
    va_list/var_access case, where regenerating the stale side alone can
    NEVER fix the pair -- the two backends' value representations are
    permanently incompatible), an unknown or degraded ``other`` producer is
    itself just ANOTHER schema-vintage-degraded fact -- regenerating that
    side too (not just the one this function was asked about) WOULD restore
    detection. Excluding on that basis silently turned "both sides are
    stale" into "clean," hiding a real, larger evidence gap -- exactly the
    P1 bug ``--require-complete-analysis`` exists to catch (a deprecated
    attribute added between two pre-v19 snapshots going undetected while
    the run still reports success). ``fact_producer``'s per-side gates
    ((a) header confirmation, (b) a known ``ast_producer``, (c) not itself
    degraded-clang) are ALL properties of that one side's own dump that a
    fresh regeneration resolves -- none of them are the PERMANENT,
    cross-side incompatibility :data:`_PAIR_PRODUCER_GATED_FLAGS` guards
    against. Only (a) -- a genuinely different EVIDENCE TIER for the
    CURRENT run (already reported through this module's sibling context-
    status axes, e.g. a ``--depth binary``/DWARF-only comparison that never
    carried header evidence at all) -- is a legitimate exclusion reason
    here; (b)/(c) are removed.
    """
    if not _other_side_is_header_confirmed(other):
        return False
    if other.ast_producer == "hybrid":
        return _other_side_has_hybrid_deprecation_provenance(other)
    return True


def _other_side_matches_snap_producer(snap: AbiSnapshot, other: AbiSnapshot) -> bool:
    """Whether *other* alone would satisfy ``diff_symbols._diff_param_
    defaults``'s/``diff_types_field_facts._diff_field_default_initializer``'s
    own same-KNOWN-producer gate against *snap* -- confirmed header
    awareness AND (when both sides' producers are positively known AND
    NEITHER is "hybrid") an EXACT match.

    Deliberately NOT excluded when either side's ``ast_producer`` is
    unknown (``None``): unlike a real, KNOWN mismatch (the permanent,
    cross-backend value-representation incompatibility this gate exists to
    guard against -- castxml's verbatim source expression vs. clang's
    fingerprint), an unknown producer on either side is itself just another
    regeneratable, schema-vintage gap (the exact round-10 principle
    :func:`_other_side_supports_known_producer_comparison` documents) --
    excluding on that basis would repeat that same bug in a new spot.

    **Round 12 correction (Codex review, PR #1209, fresh evidence):**
    deliberately NOT excluded when EITHER side's ``ast_producer`` is
    "hybrid" either, even against a real, known, differing top-level label
    (e.g. hybrid vs. pure "clang"). The real gates
    (``fact_provenance.fact_producer``'s hybrid branch) resolve producer
    PER DECLARATION for a hybrid snapshot -- a hybrid merge's per-entity
    provenance can independently land on either backend regardless of the
    snapshot's own top-level label, so a whole-snapshot label comparison
    (the round-11 shape this function started with) can wrongly EXCLUDE a
    pair whose actually-matched declaration really is same-producer at the
    per-declaration level, silently hiding a real suppressed finding --
    the DANGEROUS direction (under-tainting), unlike every other
    conservative choice in this module. Resolving the true per-declaration
    match would mean walking matched declaration pairs, the same
    per-declaration probe this module's "rollup over already-computed
    snapshot-level fields" contract declines to attempt elsewhere (see the
    module docstring's known limitations) -- so a "hybrid" producer on
    either side is treated the same as an unknown one: never excluded by
    this whole-snapshot check alone. Only a real, KNOWN mismatch between
    two NON-hybrid producers remains a permanent, excludable block.
    """
    if not _other_side_is_header_confirmed(other):
        return False
    if snap.ast_producer in (None, "hybrid") or other.ast_producer in (None, "hybrid"):
        return True
    return snap.ast_producer == other.ast_producer


#: ``param_kind_facts_reliable`` (Codex review, PR #1209 round 9, fresh
#: evidence -- disproves this module's own round-4/6 reply, which claimed
#: this flag "stays fully relevant" post-projection; see the corrected
#: module docstring above). Its one real consumer (``diff_symbols.
#: _params_differ``, reading ``Param.kind_fact``) is called ONLY from
#: ``_check_params_change``, which returns ``[]`` immediately whenever
#: ``params_unconfirmed`` -- ``diff_symbols._is_stripped_symbols_only(old)
#: or _is_stripped_symbols_only(new)`` -- before ``_params_differ`` is ever
#: reached. ``policy.depth_projection._strip_header_and_above_evidence``
#: sets exactly that shape for a non-DWARF-sourced ``--depth binary``
#: projection: every surviving function's ``params`` is cleared to ``[]``
#: and ``elf_only_mode`` is set, which is ``_is_stripped_symbols_only``'s
#: own trigger condition (confirmed by reading both functions directly, not
#: assumed). Unlike :data:`_PAIR_HEADER_ONLY_GATED_FLAGS`'s single-``other``
#: question, this one is symmetric over BOTH sides (an OR, mirroring
#: ``params_unconfirmed`` itself), so it is checked directly against
#: ``snap``/``other`` in :func:`_pair_aware_degraded_facts` rather than
#: through an ``other``-only helper.
_DEPTH_PROJECTION_PARAMS_CLEARED_FLAGS: frozenset[str] = frozenset(
    {"param_kind_facts_reliable"}
)


def _side_is_stripped_symbols_only(snap: AbiSnapshot) -> bool:
    """Mirrors ``diff_symbols._is_stripped_symbols_only`` exactly: a
    stripped, symbols-only dump (``elf_only_mode`` set, no type-level
    evidence at all) -- see that function's own docstring for the full
    per-field rationale. Duplicated rather than imported, matching this
    module's existing pattern for detector-gate logic (e.g.
    :func:`_other_side_confirms_pair_gate`'s own ``_both_header_aware``
    mirror).
    """
    if not getattr(snap, "elf_only_mode", False):
        return False
    if snap.types or snap.enums or snap.typedefs:
        return False
    dwarf = getattr(snap, "dwarf", None)
    if dwarf is not None and (dwarf.structs or dwarf.enums):
        return False
    return bool(snap.functions or snap.variables)


def _pair_params_unconfirmed(snap: AbiSnapshot, other: AbiSnapshot) -> bool:
    """Mirrors ``diff_symbols._diff_functions``'/``_diff_pointer_levels``'
    own ``params_unconfirmed = _is_stripped_symbols_only(old) or
    _is_stripped_symbols_only(new)`` computation exactly -- symmetric over
    both sides, unlike every other pair gate in this module.
    """
    return _side_is_stripped_symbols_only(snap) or _side_is_stripped_symbols_only(other)


def _pair_aware_degraded_facts(snap: AbiSnapshot, other: AbiSnapshot) -> list[str]:
    """*snap*'s own :func:`degraded_reliability_facts`, narrowed to drop a
    :data:`_PAIR_PRODUCER_GATED_FLAGS`/:data:`_PAIR_HEADER_ONLY_GATED_FLAGS`/
    :data:`_PAIR_KNOWN_DEPRECATION_PRODUCER_GATED_FLAGS`/
    :data:`_PAIR_SAME_PRODUCER_AS_SNAP_GATED_FLAGS`/
    :data:`_DEPTH_PROJECTION_PARAMS_CLEARED_FLAGS` entry whose one real
    consumer never ran for this pair because *other* doesn't also clear the
    detector's own both-sides gate (see :func:`_other_side_confirms_pair_
    gate`/:func:`_other_side_is_header_confirmed`/:func:`_other_side_
    supports_known_producer_comparison`/:func:`_other_side_matches_snap_
    producer`/:func:`_pair_params_unconfirmed`).
    """
    kept = []
    for name in degraded_reliability_facts(snap):
        producer = _PAIR_PRODUCER_GATED_FLAGS.get(name)
        if producer is not None:
            if _other_side_confirms_pair_gate(other, producer):
                kept.append(name)
            continue
        if name in _PAIR_HEADER_ONLY_GATED_FLAGS:
            if _other_side_is_header_confirmed(other):
                kept.append(name)
            continue
        if name in _PAIR_KNOWN_DEPRECATION_PRODUCER_GATED_FLAGS:
            if _other_side_supports_known_producer_comparison(other):
                kept.append(name)
            continue
        if name in _PAIR_SAME_PRODUCER_AS_SNAP_GATED_FLAGS:
            if _other_side_matches_snap_producer(snap, other):
                kept.append(name)
            continue
        if name in _DEPTH_PROJECTION_PARAMS_CLEARED_FLAGS:
            if not _pair_params_unconfirmed(snap, other):
                kept.append(name)
            continue
        kept.append(name)
    return kept


def _all_fields_equal(old: object, new: object) -> bool:
    """Deep equality over *every* field, ``compare=False`` ones included.

    Plain ``==`` on two dataclasses is not this (Codex review, PR #1228,
    fresh evidence): ``Function``/``Variable``/``RecordType``'s
    ``entity_id`` is ``field(..., compare=False)`` -- derived identity,
    deliberately excluded from equality -- yet it IS persisted and IS part
    of ``storage.snapshot_encode.snapshot_content_digest``. Two snapshots
    carrying the same declarations under different entity ids therefore
    satisfy ``old == new`` while their canonical serializations differ,
    which would let :func:`_same_content` call them one capture AND leave
    ``confidence.note_if_same_binary_compared`` (digest-driven) silent --
    the one channel this module's own docstring points at for the residual.

    Fails closed: any pair this walk cannot compare structurally (a
    mismatched type, an unwalkable container) answers ``False`` and falls
    through to the ordinary degraded report.
    """
    if type(old) is not type(new):
        return False
    if dataclasses.is_dataclass(old) and not isinstance(old, type):
        return all(
            _all_fields_equal(getattr(old, f.name), getattr(new, f.name))
            for f in dataclasses.fields(old)
        )
    if isinstance(old, (list, tuple)):
        assert isinstance(new, (list, tuple))
        return len(old) == len(new) and all(
            _all_fields_equal(a, b) for a, b in zip(old, new)
        )
    if isinstance(old, dict):
        assert isinstance(new, dict)
        return old.keys() == new.keys() and all(
            _all_fields_equal(old[k], new[k]) for k in old
        )
    return bool(old == new)


def _same_content(old: AbiSnapshot, new: AbiSnapshot) -> bool:
    """Whether *old* and *new* are provably the same content.

    :func:`_all_fields_equal` -- structural equality over every field the
    snapshot persists, including the ``compare=False`` ones plain ``==``
    skips.
    Chosen over a serialized digest (``storage.snapshot_encode.
    snapshot_content_digest``, this repo's existing content fingerprint)
    because ``policy`` may not import ``storage`` (``architecture/
    modules.yaml``: ``policy -> model, compare``). It must agree with that
    digest's notion of content, which is why it cannot be plain ``==``:
    see :func:`_all_fields_equal`. Soundness is what matters here,
    and field-wise equality has it in the direction actually used: equal
    ==> every input a pairwise detector reads agrees on both sides ==> no
    pairwise finding is possible. The converse is neither claimed nor
    needed: any inequality falls through to the ordinary degraded report,
    which is the safe direction.

    **The one residual, and where it is reported instead** (Codex review,
    PR #1228): an ``AbiSnapshot`` is a lossy capture, so equal content
    proves the two sides' *recorded evidence* is equal, not that the two
    underlying artifacts are. Two genuinely different binaries whose stale
    snapshots decode equal (the stale schema failed to record the one field
    that differs) therefore read ``"clean"`` here. That case is not
    silent, and deliberately is not this field's job: it is exactly the
    population ``confidence.note_if_same_binary_compared`` already fires
    on, from the identical signal (equal canonical serialization), with the
    stronger claim -- "this comparison cannot detect a change even if one
    was intended -- verify the correct snapshot files were provided" -- on
    ``DiffResult.coverage_warnings``, which no suppression rule can remove.
    Reporting the same residual a second time as schema staleness would
    label it as a *vintage* problem, which it is not: the comparison is
    equally blind at any vintage once both sides decode to the same
    evidence. ``tests/test_analysis_assurance_content_identity.py``'s
    ``test_content_identical_compare_still_warns_it_can_detect_nothing``
    pins the pairing, so the disclosure cannot quietly disappear and leave
    this return claiming completeness alone.
    """
    return _all_fields_equal(old, new)


def schema_staleness_status(
    old: AbiSnapshot, new: AbiSnapshot
) -> tuple[str, list[str]]:
    """``"clean"``/``"degraded"`` plus human-readable notes -- the
    ``analysis_assurance.AnalysisAssurance.schema_staleness_status`` value
    for this *old*/*new* pair.

    Same shape as ``analysis_assurance.py``'s other context-status helpers
    (``_l0_context_status``/``_header_context_status``/etc.), but with no
    ``"asymmetric"`` state of its own: unlike header/DWARF/L3 evidence (each
    gated on BOTH sides carrying the same channel), a *single* side's stale
    fact already means the affected detector(s) declined to trust it for
    THIS comparison, whether or not the other side is current -- except the
    :data:`_PAIR_PRODUCER_GATED_FLAGS`/:data:`_PAIR_HEADER_ONLY_GATED_FLAGS`/
    :data:`_PAIR_KNOWN_DEPRECATION_PRODUCER_GATED_FLAGS`/
    :data:`_PAIR_SAME_PRODUCER_AS_SNAP_GATED_FLAGS` flags, which
    :func:`_pair_aware_degraded_facts` narrows first.

    ``old is new`` (real Python object identity, not merely equal content)
    is a self-diff -- the exact shape ``workflows.no_baseline_compare``'s
    audit path builds via ``_diff_pair(new, new, ...)`` to reuse the
    ordinary comparison machinery for a candidate with no real baseline,
    then asserts the *comparison* half of the resulting changes is empty
    and discards it, keeping only the candidate-side hygiene/pattern-scan
    findings (Codex review, PR #1209 round 6). Every ``*_facts_reliable``
    flag exists to guard exactly one thing -- a false PAIRWISE finding from
    comparing two independently-extracted sides whose evidence generations
    differ -- and comparing a value against itself can never produce one,
    reliable or not, the identical reason ``_l0_context_status``/
    ``_header_context_status``/``_dwarf_context_status``/etc. are all
    already trivially ``"clean"``/``"not_evaluated"`` under self-pairing
    (their own OLD-vs-NEW agreement checks are vacuously true). Reading
    ``old``/``new`` as two distinct sides here -- reporting the one
    candidate's own staleness as BOTH "old snapshot" and "new snapshot" --
    would otherwise be the one context-status field self-pairing does NOT
    make safe by construction, purely because it asks a per-side question
    ("is THIS side's flag False") rather than a cross-side agreement
    question. That argument never depended on object
    identity, and an earlier revision of this docstring wrongly carved the
    two-operand case out ("two separate parses are always two separate
    objects"). ONE stored snapshot file loaded twice -- ``compare
    baseline.abi.json baseline.abi.json``, or a CI job re-checking an
    unchanged cached baseline -- produces two distinct objects whose
    content is nevertheless equal, and reporting that as ``"degraded"``
    (flipping ``assurance.status`` complete -> partial, newly failing a job
    gating on it) makes exactly the claim the ``old is new`` return already
    rejects.

    So the early return is widened from object identity to *provable
    content identity* (:func:`_same_content`). That test is SOUND, not
    heuristic: equal content means every input the pairwise detectors read
    is equal on both sides, so no pairwise finding is possible, reliable
    facts or not. It is deliberately NOT widened to "same input path" or
    "same binary": two independent extractions of one binary can
    legitimately differ in schema vintage, which is precisely the case this
    field exists to report. It is also evaluated only AFTER
    :func:`_pair_aware_degraded_facts` has returned something for at least
    one side, so an ordinary clean comparison never pays for it; that path
    is rare by construction.
    """
    if old is new:
        return "clean", []
    old_degraded = _pair_aware_degraded_facts(old, new)
    new_degraded = _pair_aware_degraded_facts(new, old)
    if not old_degraded and not new_degraded:
        return "clean", []
    if _same_content(old, new):
        return "clean", []
    notes: list[str] = []
    if old_degraded:
        notes.append(
            "old snapshot carries schema-vintage-degraded facts (regenerate "
            f"with the current abicheck to restore full detection): "
            f"{', '.join(old_degraded)}"
        )
    if new_degraded:
        notes.append(
            "new snapshot carries schema-vintage-degraded facts (regenerate "
            f"with the current abicheck to restore full detection): "
            f"{', '.join(new_degraded)}"
        )
    return "degraded", notes
