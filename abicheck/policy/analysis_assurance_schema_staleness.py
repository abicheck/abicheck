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
degraded fact (``serialization.decode_snapshot``) -- but that warning is
stderr-only, invisible to any programmatic consumer of the JSON report, and
``analysis_assurance``'s own completeness rollup had no signal for it at
all: a run with one or more degraded facts still read
``run_outcome.assurance.status == "complete"``. This module is that
missing signal, computed from the exact same table the load-time warning
uses (via ``degraded_reliability_facts``) so the two can never
independently drift on what counts as "degraded".

**Known, accepted limitation (Codex review, PR #1209):**
``clang_field_initializer_facts_reliable``'s True downstream cost is
per-declaration and value-shape-dependent
(``diff_default_value_reliability._fingerprint_comparison_unreliable``
only actually suppresses a comparison when the two sides' fingerprint
*generations* differ AND the specific field's own value is fingerprint-
shaped -- two same-vintage legacy snapshots compare their fingerprints
just fine). Modeling that accurately would mean walking every field's own
resolved value/producer here, which conflicts with this module's (and the
pre-existing load-time warning's) deliberate "rollup over already-computed
snapshot-level fields, never a new per-declaration probe" contract -- see
``analysis_assurance.py``'s own module docstring. Left conservative (a
False flag always taints, whichever the pair) rather than attempting an
incomplete pair-aware model: the failure direction is safe (a spurious
``"degraded"`` under-claims confidence; it can never fabricate a
``"complete"`` claim the P1 bug this module exists to fix was about).

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

#: The two flags whose one real consumer requires BOTH sides confirmed
#: (non-inferred) header-aware -- ``_both_header_aware`` -- but, unlike
#: :data:`_PAIR_PRODUCER_GATED_FLAGS`, places no further requirement on the
#: *other* side's producer (Codex review, PR #1209 round 7, fresh evidence):
#: ``diff_symbols._diff_param_restrict`` exits at ``_both_header_aware``
#: before ever reading ``clang_restrict_facts_reliable``, and ``diff_types_
#: field_facts._diff_field_default_initializer`` does the same before its
#: own per-field ``fact_same_producer_qualified`` gate for ``clang_field_
#: initializer_facts_reliable`` -- both documented as cross-producer-safe
#: (restrict) or handled by a separate, deliberately conservative per-
#: declaration limitation (field initializer -- see this module's own
#: docstring) once both sides are confirmed header-aware. ``clang_
#: deprecation_facts_reliable`` moved OUT of this set in round 8 -- see
#: :data:`_PAIR_KNOWN_DEPRECATION_PRODUCER_GATED_FLAGS` below, its own real
#: consumer needs more than header confirmation alone.
_PAIR_HEADER_ONLY_GATED_FLAGS: frozenset[str] = frozenset(
    {
        "clang_field_initializer_facts_reliable",
        "clang_restrict_facts_reliable",
    }
)

#: ``clang_deprecation_facts_reliable`` (Codex review, PR #1209 round 8,
#: fresh evidence): ``diff_symbols._diff_func_deprecated`` calls
#: ``fact_provenance.fact_producer`` independently on BOTH sides and skips
#: the pair entirely if either call returns ``None`` -- which it does
#: whenever *that* side isn't confirmed header-aware (the
#: :data:`_PAIR_HEADER_ONLY_GATED_FLAGS` half of the gate) OR that side's
#: own ``ast_producer`` isn't positively known (``None`` -- a legacy
#: snapshot that predates provenance tracking entirely) OR that side is
#: ITSELF a degraded, confirmed-header "clang" producer for this exact fact
#: family (``fact_producer``'s own ``ast_producer == "clang" and not
#: clang_deprecation_facts_reliable`` exclusion for a ``:deprecated``/
#: ``:is_scoped`` key -- in which case no comparison can structurally run
#: for either side's sake, so reporting "degraded" here would be a spurious
#: signal, not a conservative one). Mirrored exactly in :func:`_other_side_
#: supports_known_producer_comparison` rather than approximated with a bare
#: header-confirmation check, which a confirmed-header-but-unknown-producer
#: (or itself-degraded-clang) ``other`` would incorrectly still taint.
_PAIR_KNOWN_DEPRECATION_PRODUCER_GATED_FLAGS: frozenset[str] = frozenset(
    {"clang_deprecation_facts_reliable"}
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
    """
    return any(
        key.endswith(":deprecated") or key.endswith(":is_scoped")
        for key in other.fact_provenance
    )


def _other_side_supports_known_producer_comparison(other: AbiSnapshot) -> bool:
    """Whether ``fact_provenance.fact_producer(other, <a deprecated/
    is_scoped key>)`` could resolve non-``None`` for SOME declaration --
    mirroring that function's own gating logic exactly (confirmed header
    awareness, a positively known ``ast_producer``, not itself an
    unreliable confirmed-header "clang" producer for this same fact family,
    and -- for a "hybrid" producer specifically -- an actually-recorded
    per-declaration provenance entry, see :func:`_other_side_has_hybrid_
    deprecation_provenance`) rather than approximating it with header
    confirmation alone.
    """
    if not _other_side_is_header_confirmed(other):
        return False
    if other.ast_producer == "clang" and not other.clang_deprecation_facts_reliable:
        return False
    if other.ast_producer == "hybrid":
        return _other_side_has_hybrid_deprecation_provenance(other)
    return other.ast_producer in ("castxml", "clang")


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
    :data:`_DEPTH_PROJECTION_PARAMS_CLEARED_FLAGS` entry whose one real
    consumer never ran for this pair because *other* doesn't also clear the
    detector's own both-sides gate (see :func:`_other_side_confirms_pair_
    gate`/:func:`_other_side_is_header_confirmed`/:func:`_other_side_
    supports_known_producer_comparison`/:func:`_pair_params_unconfirmed`).
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
        if name in _DEPTH_PROJECTION_PARAMS_CLEARED_FLAGS:
            if not _pair_params_unconfirmed(snap, other):
                kept.append(name)
            continue
        kept.append(name)
    return kept


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
    :data:`_PAIR_KNOWN_DEPRECATION_PRODUCER_GATED_FLAGS` flags, which
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
    question. A user-supplied ``compare foo.so foo.so`` (two independently
    parsed, merely content-identical snapshots) never hits this: real
    identity, not equal content, is what this checks, and two separate
    parses are always two separate objects.
    """
    if old is new:
        return "clean", []
    old_degraded = _pair_aware_degraded_facts(old, new)
    new_degraded = _pair_aware_degraded_facts(new, old)
    if not old_degraded and not new_degraded:
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
