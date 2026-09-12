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

"""``_classify_outcome``'s OUTCOME_COORDINATES_ONLY property test (ADR-048
amendment) -- split out of ``test_graph_reconcile.py`` purely to keep that
file under the architecture gate's per-file test line-count cap (mirrors
``test_graph_reconcile_closure_rename.py``'s own split for the same reason).

Three PR review rounds (Codex) progressively narrowed
``OUTCOME_COORDINATES_ONLY`` from "neither renamed nor moved" down to its
current, correct gate: the raw qualified name must have actually differed
and normalized away (positive coordinate-churn evidence, not an
already-identical name that proves nothing), the signature tail must still
agree (catches a simultaneous real signature change WHEN tracked), and the
node kind must be type-shaped (a function/variable's signature isn't
exposed to ``resolve_identity_for_node`` by the real
``source_graph_build_source_abi.py`` producer, so that axis is vacuous for
a ``source_decl`` and can't prove nothing else changed). See
``graph_reconcile._classify_outcome``'s own docstring/comments and
ADR-048's 2026-09 amendment for the full account.
"""

from __future__ import annotations

from abicheck.buildsource.entity_identity import (
    IDENTITY_TIER_CANONICAL,
    CanonicalIdentity,
)
from abicheck.buildsource.graph_reconcile import (
    _OUTCOME_PROSE,
    OUTCOME_COORDINATES_ONLY,
    OUTCOME_MOVED,
    OUTCOME_RECONCILED,
    OUTCOME_RENAMED,
    _classify_outcome,
)


def _identity(
    qn: str, decl_file: str, sig: str = "", kind: str = "record_type"
) -> CanonicalIdentity:
    return CanonicalIdentity(
        primary_id=f"id:{qn}",
        tier=IDENTITY_TIER_CANONICAL,
        qualified_name=qn,
        source_relative=decl_file,
        normalized_signature=sig,
        kind=kind,
    )


def test_classify_outcome_prose_is_truthful_about_what_changed() -> None:
    """Bug-class regression: PR #1204's fallthrough lumped coordinate churn
    with the opposite "both changed" outcome; review found COORDINATES_ONLY
    needs POSITIVE churn evidence, an agreeing signature tail, AND a
    type-shaped kind (a source_decl's signature isn't tracked in
    production, so it can't prove nothing else changed) -- not just both
    predicates False. Oracle table, independent of the code tested."""
    lam1, lam2 = "(lambda at f.h:1:2)", "(lambda at f.h:9:9)"
    names = {
        "identical": (
            "ns::Widget",
            "ns::Widget",
            False,
            False,
            ("s", "s"),
            "record_type",
        ),
        "real_rename": (
            "ns::Widget",
            "ns::WidgetV2",
            True,
            False,
            ("s", "s"),
            "record_type",
        ),
        "coord_shift": (lam1, lam2, False, True, ("s", "s"), "record_type"),
        "coord_shift_sig_change": (
            lam1,
            lam2,
            False,
            True,
            ("s0", "s1"),
            "record_type",
        ),
        "coord_shift_source_decl": (lam1, lam2, False, True, ("s", "s"), "source_decl"),
    }
    files = {"same": ("a.h", "a.h", False), "real_move": ("a.h", "b.h", True)}
    name_words, loc_words = ("renamed", "name"), ("moved", "location", "declaring")
    for nk, (
        old_qn,
        new_qn,
        name_ch,
        has_coord,
        (old_sig, new_sig),
        kind,
    ) in names.items():
        for fk, (old_f, new_f, file_ch) in files.items():
            old_id = _identity(old_qn, old_f, f"sig:{old_qn}\x1f{old_sig}", kind)
            new_id = _identity(new_qn, new_f, f"sig:{new_qn}\x1f{new_sig}", kind)
            outcome = _classify_outcome(old_id, new_id)
            if name_ch and not file_ch:
                expected = OUTCOME_RENAMED
            elif file_ch and not name_ch:
                expected = OUTCOME_MOVED
            elif name_ch and file_ch:
                expected = OUTCOME_RECONCILED
            elif has_coord and old_sig == new_sig and kind != "source_decl":
                expected = OUTCOME_COORDINATES_ONLY
            else:
                expected = OUTCOME_RECONCILED
            assert outcome == expected, f"{nk}/{fk}: {expected!r} != {outcome!r}"
            prose = _OUTCOME_PROSE[outcome]
            # RECONCILED w/ neither axis changed is ADR-048's accepted
            # "no clean split" (case197) prose overstatement, not a bug.
            if outcome == OUTCOME_RECONCILED and not (name_ch and file_ch):
                continue
            if not name_ch:
                assert not any(w in prose for w in name_words), (nk, fk, outcome, prose)
            if not file_ch:
                assert not any(w in prose for w in loc_words), (nk, fk, outcome, prose)


def test_coordinate_only_requires_both_names_present() -> None:
    """Codex review, fresh evidence: `bool(old_qn)/bool(new_qn)` mirrors
    `renamed`'s own guard -- an absent qualified name on either side (a real
    ``GraphNode.from_dict()`` shape, per its own permissive label default)
    is a name gain/loss, not coordinate-churn evidence, even though
    `old_qn != new_qn` is trivially true against an empty string."""
    old_id = _identity("", "a.h", kind="record_type")
    new_id = _identity("(lambda at f.h:1:2)", "a.h", kind="record_type")
    assert _classify_outcome(old_id, new_id) == OUTCOME_RECONCILED


def test_coordinate_only_does_not_require_declaring_file_evidence() -> None:
    """Bug-class regression (inverts an earlier guard): absent
    ``def_file``/``SOURCE_DECLARES`` evidence must NOT push a pair with
    positive, name-embedded coordinate-churn evidence into the strictly
    stronger ``OUTCOME_RECONCILED`` ("both name and location evidence
    changed"). ``moved`` is already False from that same absent evidence,
    so the old guard declined the weak claim only to make the strong one --
    and it made ``OUTCOME_COORDINATES_ONLY`` unreachable in production (0
    occurrences repo-wide; 15 of 15 otherwise-eligible oneTBB header-graph
    pairs blocked solely by ``old_file == new_file == ""``)."""
    old_id = _identity("(lambda at f.h:1:2)", "", kind="record_type")
    new_id = _identity("(lambda at f.h:9:9)", "", kind="record_type")
    assert _classify_outcome(old_id, new_id) == OUTCOME_COORDINATES_ONLY


#: Name pairs, each tagged with the two *independent* dimensions an oracle
#: needs: did the location-free name change (a real rename), and is there
#: positive coordinate-churn evidence (raw names differ, both present,
#: normalized equal)?
_NAME_CASES: dict[str, tuple[str, str, bool, bool]] = {
    "identical": ("ns::Widget", "ns::Widget", False, False),
    "real_rename": ("ns::Widget", "ns::WidgetV2", True, False),
    "coord_shift": ("(lambda at f.h:1:2)", "(lambda at f.h:9:9)", False, True),
    "old_name_absent": ("", "(lambda at f.h:9:9)", False, False),
    "new_name_absent": ("(lambda at f.h:1:2)", "", False, False),
}

#: Declaring-file pairs, tagged with whether a *move* is shown (both sides
#: present and differing) and whether file evidence is present at all.
_FILE_CASES: dict[str, tuple[str, str, bool, bool]] = {
    "same_file": ("a.h", "a.h", False, True),
    "real_move": ("a.h", "b.h", True, True),
    "old_file_absent": ("", "b.h", False, False),
    "new_file_absent": ("a.h", "", False, False),
    "no_file_evidence": ("", "", False, False),
}

_SIG_CASES: dict[str, tuple[str, str]] = {
    "same_sig": ("s", "s"),
    "sig_change": ("s0", "s1"),
}
_KIND_CASES = ("record_type", "enum_type", "typedef", "source_decl")


def _expected_outcome(
    *,
    name_changed: bool,
    has_coord: bool,
    file_changed: bool,
    same_sig: bool,
    kind: str,
) -> str:
    """Oracle stated from the four input DIMENSIONS, not from the
    implementation's own predicate expressions."""
    if name_changed and not file_changed:
        return OUTCOME_RENAMED
    if file_changed and not name_changed:
        return OUTCOME_MOVED
    if name_changed and file_changed:
        return OUTCOME_RECONCILED
    if has_coord and same_sig and kind in ("record_type", "enum_type", "typedef"):
        return OUTCOME_COORDINATES_ONLY
    return OUTCOME_RECONCILED


def _generated_cases() -> list[tuple[str, CanonicalIdentity, CanonicalIdentity, str]]:
    cases = []
    for nk, (old_qn, new_qn, name_ch, has_coord) in _NAME_CASES.items():
        for fk, (old_f, new_f, file_ch, _has_file) in _FILE_CASES.items():
            for sk, (old_sig, new_sig) in _SIG_CASES.items():
                for kind in _KIND_CASES:
                    old_id = _identity(
                        old_qn, old_f, f"sig:{old_qn}\x1f{old_sig}", kind
                    )
                    new_id = _identity(
                        new_qn, new_f, f"sig:{new_qn}\x1f{new_sig}", kind
                    )
                    expected = _expected_outcome(
                        name_changed=name_ch,
                        has_coord=has_coord,
                        file_changed=file_ch,
                        same_sig=old_sig == new_sig,
                        kind=kind,
                    )
                    cases.append((f"{nk}/{fk}/{sk}/{kind}", old_id, new_id, expected))
    return cases


class TestClassifyOutcomeProperties:
    """``_classify_outcome`` is a reusable classification primitive, so it
    gets the standalone property-test treatment AGENTS.md's
    "Primitive-level property tests" bullet prescribes (the same treatment
    ``TestPairedStableIndicesProperties`` gives ``_paired_stable_indices``)
    -- invariants over an exhaustively enumerated small input domain (200
    pairs across four independent dimensions), not a fixture pinned to the
    one observed oneTBB input. Reachability is itself one of the
    invariants: the defect this class was written for was an outcome no
    production input could ever produce."""

    def test_every_pair_gets_exactly_one_of_the_four_outcomes(self) -> None:
        outcomes = {
            OUTCOME_RENAMED,
            OUTCOME_MOVED,
            OUTCOME_RECONCILED,
            OUTCOME_COORDINATES_ONLY,
        }
        for label, old_id, new_id, _ in _generated_cases():
            assert _classify_outcome(old_id, new_id) in outcomes, label

    def test_all_four_outcomes_are_reachable(self) -> None:
        """The reachability half -- exactly what this defect was: the
        declaring-file guard made ``OUTCOME_COORDINATES_ONLY`` producible
        by no real input at all, and nothing failed anywhere."""
        seen = {_classify_outcome(o, n) for _, o, n, _ in _generated_cases()}
        assert seen == {
            OUTCOME_RENAMED,
            OUTCOME_MOVED,
            OUTCOME_RECONCILED,
            OUTCOME_COORDINATES_ONLY,
        }

    def test_outcome_matches_dimension_oracle(self) -> None:
        for label, old_id, new_id, expected in _generated_cases():
            assert _classify_outcome(old_id, new_id) == expected, label

    def test_never_claims_a_move_without_two_sided_file_evidence(self) -> None:
        """No label may claim a dimension the inputs do not show changed.
        ``OUTCOME_MOVED`` and ``OUTCOME_RECONCILED`` both assert the
        declaring location changed, which absent file evidence cannot
        show -- so neither may be returned for a pair that carries
        positive coordinate-churn evidence and is kind/signature
        eligible. (A pair with NO positive evidence on either axis still
        falls through to ``OUTCOME_RECONCILED``: ADR-048's accepted
        "no clean split" prose overstatement, see
        ``test_classify_outcome_prose_is_truthful_about_what_changed``.)"""
        for nk, (old_qn, new_qn, name_ch, has_coord) in _NAME_CASES.items():
            for fk, (old_f, new_f, _file_ch, has_file) in _FILE_CASES.items():
                if has_file:
                    continue
                for kind in _KIND_CASES:
                    old_id = _identity(old_qn, old_f, f"sig:{old_qn}\x1fs", kind)
                    new_id = _identity(new_qn, new_f, f"sig:{new_qn}\x1fs", kind)
                    outcome = _classify_outcome(old_id, new_id)
                    assert outcome != OUTCOME_MOVED, (nk, fk, kind)
                    if has_coord and kind != "source_decl":
                        assert outcome == OUTCOME_COORDINATES_ONLY, (nk, fk, kind)
                    assert not name_ch or outcome == OUTCOME_RENAMED, (nk, fk, kind)

    def test_side_swap_symmetry(self) -> None:
        """Every one of the four outcomes is a symmetric relation over the
        pair: swapping old and new must not change the label (the kind
        restriction reads ``old_identity.kind``, so a swap keeps it equal
        only because both sides share a kind -- which is the only shape a
        real reconciled pair has, since matching is per kind group)."""
        for label, old_id, new_id, _ in _generated_cases():
            assert _classify_outcome(old_id, new_id) == _classify_outcome(
                new_id, old_id
            ), label

    def test_source_decl_never_returns_coordinates_only(self) -> None:
        """Kind restriction holds regardless of every other dimension: a
        ``source_decl``'s signature isn't tracked by the production
        producer, so "nothing else changed" is unprovable for one."""
        for label, old_id, new_id, _ in _generated_cases():
            if old_id.kind != "source_decl":
                continue
            assert _classify_outcome(old_id, new_id) != OUTCOME_COORDINATES_ONLY, label


#: Name pairs whose *embedded marker basename* differs -- positive
#: name-carried evidence of a cross-file move, which
#: `closure_location_free_identity` collapses along with the coordinates.
_MARKER_MOVE_CASES: dict[str, tuple[str, str]] = {
    "paren_at_spelling": ("w<(lambda at old.h:1:2)>", "w<(lambda at new.h:9:9)>"),
    "normalized_spelling": ("w<(lambda:old.h:1:2)>", "w<(lambda:new.h:9:9)>"),
    "bare_unnamed_struct": (
        "unnamed struct at old.h:56:5",
        "unnamed struct at new.h:61:5",
    ),
    "anonymous_union": (
        "anonymous union at old.h:649:9",
        "anonymous union at new.h:651:9",
    ),
    "absolute_paths": (
        "w<(lambda at /src/a/old.h:1:2)>",
        "w<(lambda at /src/a/new.h:9:9)>",
    ),
}

#: The complement: the marker basename is UNCHANGED, only its coordinates
#: move -- including the two-checkout-root spelling, which must not read as
#: a move just because the absolute paths differ.
_MARKER_SAME_FILE_CASES: dict[str, tuple[str, str]] = {
    "paren_at_spelling": ("w<(lambda at f.h:1:2)>", "w<(lambda at f.h:9:9)>"),
    "normalized_spelling": ("w<(lambda:f.h:1:2)>", "w<(lambda:f.h:9:9)>"),
    "differing_checkout_roots": (
        "unnamed struct at /old/checkout/f.h:56:5",
        "unnamed struct at /new/checkout/f.h:61:5",
    ),
    "two_markers_both_stable": (
        "p<(lambda at f.h:1:2),(lambda at g.h:3:4)>",
        "p<(lambda at f.h:8:2),(lambda at g.h:9:4)>",
    ),
}


class TestMarkerCarriedLocationEvidence:
    """Codex review (PR #1228, fresh evidence): dropping the declaring-file
    gate opened a hole the gate had been covering.
    ``closure_location_free_identity`` drops each marker's *basename* as
    well as its ``:line:col``, and its own docstring justifies that by
    saying a genuine cross-FILE move is "separately caught by
    ``_classify_outcome``'s own file-based ``moved`` check" -- true only
    while such evidence exists. With none recorded, ``old.h`` -> ``new.h``
    collapsed to nothing and a real move read as the *compatible*
    coordinate-only kind.

    Stated as the general invariant rather than the one reported pair: over
    every marker spelling the identity vocabulary accepts, a differing
    marker basename is a move and an agreeing one is not, and the answer
    never depends on the checkout root the path happens to carry."""

    def test_differing_marker_basename_is_a_move(self) -> None:
        for label, (old_qn, new_qn) in _MARKER_MOVE_CASES.items():
            outcome = _classify_outcome(
                _identity(old_qn, "", f"sig:{old_qn}\x1fs"),
                _identity(new_qn, "", f"sig:{new_qn}\x1fs"),
            )
            assert outcome == OUTCOME_MOVED, (label, outcome)

    def test_agreeing_marker_basename_is_not_a_move(self) -> None:
        for label, (old_qn, new_qn) in _MARKER_SAME_FILE_CASES.items():
            outcome = _classify_outcome(
                _identity(old_qn, "", f"sig:{old_qn}\x1fs"),
                _identity(new_qn, "", f"sig:{new_qn}\x1fs"),
            )
            assert outcome == OUTCOME_COORDINATES_ONLY, (label, outcome)

    def test_marker_evidence_is_symmetric(self) -> None:
        """Swapping the sides cannot change which file moved-ness holds."""
        for label, (old_qn, new_qn) in {
            **_MARKER_MOVE_CASES,
            **_MARKER_SAME_FILE_CASES,
        }.items():
            old_id = _identity(old_qn, "", f"sig:{old_qn}\x1fs")
            new_id = _identity(new_qn, "", f"sig:{new_qn}\x1fs")
            assert _classify_outcome(old_id, new_id) == _classify_outcome(
                new_id, old_id
            ), label

    def test_real_declaring_file_evidence_wins_over_the_marker(self) -> None:
        """Marker evidence is a fallback, not an override (Codex review,
        PR #1229): two-sided declaring files are the stronger evidence
        about where the DECLARATION lives, and a marker inside the name may
        describe a nested template argument's location instead --
        `Wrapper<(lambda at old.h:1:2)>` and `Wrapper<(lambda at
        new.h:9:9)>` both declared in `wrapper.h` did not move.

        Nor is it a coordinate-only shift: the markers naming different
        files means something beyond coordinates changed, so the pair
        falls through rather than claiming no material identity change."""
        old_qn, new_qn = _MARKER_MOVE_CASES["paren_at_spelling"]
        outcome = _classify_outcome(
            _identity(old_qn, "wrapper.h", f"sig:{old_qn}\x1fs"),
            _identity(new_qn, "wrapper.h", f"sig:{new_qn}\x1fs"),
        )
        assert outcome == OUTCOME_RECONCILED, outcome

    def test_two_sided_files_decide_the_move_in_both_directions(self) -> None:
        """The complement: when the declaring files themselves differ, that
        is the move — with or without a marker disagreeing."""
        stable_qn = "w<(lambda at f.h:1:2)>"
        shifted_qn = "w<(lambda at f.h:9:9)>"
        assert (
            _classify_outcome(
                _identity(stable_qn, "a.h", f"sig:{stable_qn}\x1fs"),
                _identity(shifted_qn, "b.h", f"sig:{shifted_qn}\x1fs"),
            )
            == OUTCOME_MOVED
        )
        assert (
            _classify_outcome(
                _identity(stable_qn, "a.h", f"sig:{stable_qn}\x1fs"),
                _identity(shifted_qn, "a.h", f"sig:{shifted_qn}\x1fs"),
            )
            == OUTCOME_COORDINATES_ONLY
        )

    def test_one_sided_file_evidence_falls_back_to_the_marker(self) -> None:
        """The fallback boundary: one recorded declaring file is not a
        two-sided comparison, so the marker still speaks — provided that
        file does not contradict its own side's marker (the case below)."""
        old_qn, new_qn = _MARKER_MOVE_CASES["paren_at_spelling"]
        for old_file, new_file in (("old.h", ""), ("", "new.h")):
            outcome = _classify_outcome(
                _identity(old_qn, old_file, f"sig:{old_qn}\x1fs"),
                _identity(new_qn, new_file, f"sig:{new_qn}\x1fs"),
            )
            assert outcome == OUTCOME_MOVED, (old_file, new_file, outcome)

    def test_a_lone_declaring_file_can_disprove_the_marker(self) -> None:
        """Codex review (PR #1229): a recorded declaring file naming NONE
        of its side's markers disproves that those markers describe the
        declaration. `Wrapper<(lambda at nested_old.h:1:2)>` declared in
        `include/wrapper.h` carries a marker for a nested template
        argument, so a differing marker on the other side cannot establish
        that the WRAPPER moved.

        The same rule the two-sided case already applies, extended to the
        one recorded file: it is still enough to disprove the fallback,
        since it is the only known declaration location. The pair keeps
        the evidence-conflict outcome rather than dropping into
        coordinate-only — the nested argument's own file really did
        change — and it holds whichever side recorded the file."""
        old_qn = "Wrapper<(lambda at nested_old.h:1:2)>"
        new_qn = "Wrapper<(lambda at nested_new.h:1:2)>"
        for old_file, new_file in (
            ("include/wrapper.h", ""),
            ("", "include/wrapper.h"),
        ):
            outcome = _classify_outcome(
                _identity(old_qn, old_file, f"sig:{old_qn}\x1fs"),
                _identity(new_qn, new_file, f"sig:{new_qn}\x1fs"),
            )
            assert outcome == OUTCOME_RECONCILED, (old_file, new_file, outcome)

    def test_a_lone_agreeing_declaring_file_still_lets_the_marker_speak(self) -> None:
        """The must-stay-distinct half, so the rule cannot degrade into
        "any recorded file silences the markers": when the recorded file IS
        one of its side's markers, the marker describes the declaration and
        a differing marker on the other side is still a move. Over every
        marker spelling, and both orientations."""
        for label, (old_qn, new_qn) in _MARKER_MOVE_CASES.items():
            for old_file, new_file in (("old.h", ""), ("", "new.h")):
                outcome = _classify_outcome(
                    _identity(old_qn, old_file, f"sig:{old_qn}\x1fs"),
                    _identity(new_qn, new_file, f"sig:{new_qn}\x1fs"),
                )
                assert outcome == OUTCOME_MOVED, (label, old_file, new_file, outcome)

    def test_a_marker_move_never_reports_coordinate_evidence(self) -> None:
        """`coordinate_evidence` answers None for anything that is not a
        coordinate-only pair -- a marker-carried move included, so it can
        never be labelled with a weak-evidence disclosure it does not
        need."""
        from abicheck.buildsource.graph_reconcile_outcome import coordinate_evidence

        for label, (old_qn, new_qn) in _MARKER_MOVE_CASES.items():
            assert (
                coordinate_evidence(
                    _identity(old_qn, "", f"sig:{old_qn}\x1fs"),
                    _identity(new_qn, "", f"sig:{new_qn}\x1fs"),
                )
                is None
            ), label


class TestMixedMarkerSpellings:
    """Codex review (PR #1229): `closure_marker_locations` ran one scan per
    accepted spelling and concatenated, so a mixed-spelling identity
    returned its markers grouped by which regex matched rather than in
    source order — and the raw form's own path group could run greedily
    through a following normalized marker. Both make the positional
    comparison answer differently for the same two markers written in a
    different order.

    The invariant, not the one reported pair: the extracted marker files
    are exactly the declaring basenames in source order, whatever mix of
    spellings the identity happens to use."""

    #: The same two markers (`a.h` then `b.h`), written every way the
    #: identity vocabulary accepts.
    _SPELLINGS = (
        "Pair<(lambda at a.h:1:2),(lambda at b.h:3:4)>",
        "Pair<(lambda:a.h:1:2),(lambda:b.h:3:4)>",
        "Pair<(lambda at a.h:1:2),(lambda:b.h:3:4)>",
        "Pair<(lambda:a.h:1:2),(lambda at b.h:3:4)>",
        "Pair<(lambda at /x/a.h:1:2),(lambda:b.h:3:4)>",
    )

    def test_every_spelling_extracts_the_same_ordered_basenames(self) -> None:
        from abicheck.model.graph_identity import closure_marker_locations

        for spelling in self._SPELLINGS:
            assert closure_marker_locations(spelling) == (
                ("lambda", "a.h", "1", "2"),
                ("lambda", "b.h", "3", "4"),
            ), spelling

    def test_order_is_source_order_not_scan_order(self) -> None:
        """The reversed identity must extract the reversed tuple — a
        grouped-by-regex result would return the same tuple for both."""
        from abicheck.model.graph_identity import closure_marker_locations

        assert closure_marker_locations(
            "Pair<(lambda at a.h:1:2),(lambda:b.h:3:4)>"
        ) == (("lambda", "a.h", "1", "2"), ("lambda", "b.h", "3", "4"))
        assert closure_marker_locations(
            "Pair<(lambda:b.h:3:4),(lambda at a.h:1:2)>"
        ) == (("lambda", "b.h", "3", "4"), ("lambda", "a.h", "1", "2"))

    def test_mixed_spellings_of_one_unmoved_pair_are_not_a_move(self) -> None:
        """The consumer half: two coordinate-only versions referencing the
        same `a.h`/`b.h` markers must not be classified as moved merely
        because the two sides spell the markers differently."""
        for old_qn in self._SPELLINGS:
            for new_qn in self._SPELLINGS:
                shifted = new_qn.replace(":1:2", ":7:8").replace(":3:4", ":9:10")
                outcome = _classify_outcome(
                    _identity(old_qn, "", "sig:x\x1fs"),
                    _identity(shifted, "", "sig:x\x1fs"),
                )
                assert outcome == OUTCOME_COORDINATES_ONLY, (old_qn, shifted, outcome)


class TestRenameAndMoveTogether:
    """Codex review (PR #1229): gating the marker-move check on
    `not renamed` disabled the only available move evidence exactly when a
    declaration was renamed AND moved, emitting the catalog's combined
    case (`declaration_identity_reconciled`) as a bare
    `declaration_renamed`."""

    def test_rename_plus_marker_move_is_reconciled(self) -> None:
        outcome = _classify_outcome(
            _identity("Old<(lambda at old.h:1:2)>", "", "sig:o\x1fs"),
            _identity("New<(lambda at new.h:9:9)>", "", "sig:n\x1fs"),
        )
        assert outcome == OUTCOME_RECONCILED, outcome

    def test_rename_without_a_marker_move_stays_renamed(self) -> None:
        """The negative control: the same rename with the marker file
        unchanged is a rename and nothing more."""
        outcome = _classify_outcome(
            _identity("Old<(lambda at f.h:1:2)>", "", "sig:o\x1fs"),
            _identity("New<(lambda at f.h:9:9)>", "", "sig:n\x1fs"),
        )
        assert outcome == OUTCOME_RENAMED, outcome

    def test_unalignable_marker_counts_make_no_move_claim(self) -> None:
        """A rename that also changes how many markers the name carries
        gives no positional correspondence to compare, so no move is
        claimed from marker text alone."""
        outcome = _classify_outcome(
            _identity("Old<(lambda at old.h:1:2)>", "", "sig:o\x1fs"),
            _identity(
                "New<(lambda at new.h:9:9),(lambda at other.h:1:1)>", "", "sig:n\x1fs"
            ),
        )
        assert outcome == OUTCOME_RENAMED, outcome


class TestQuotedMarkerLookalikes:
    """Codex review (PR #1229): `closure_location_free_identity` protects
    `"..."` quoted spans — a C++ fixed-string NTTP spelling marker-shaped
    text is source *content*, not a declaring location — but the marker
    extraction did not, so the two functions disagreed about which text is
    a marker. A quoted lookalike changing therefore read as a location
    change, and a genuine rename reported the combined outcome.

    Stated as the agreement invariant between the two complements, which
    is the property that must hold for any identity, rather than only for
    the reported pair."""

    _QUOTED_ONLY = (
        ('Tag<"lambda:a.h:1:2">', 'Tag<"lambda:b.h:3:4">'),
        ('Tag<"lambda at a.h:1:2">', 'Tag<"lambda at b.h:3:4">'),
        ('N<"unnamed struct at a.h:5:5">', 'N<"unnamed struct at b.h:6:6">'),
    )

    def test_quoted_lookalikes_carry_no_marker_evidence(self) -> None:
        from abicheck.model.graph_identity import closure_marker_locations

        for old_qn, new_qn in self._QUOTED_ONLY:
            assert closure_marker_locations(old_qn) == (), old_qn
            assert closure_marker_locations(new_qn) == (), new_qn

    def test_a_rename_spelled_only_in_quotes_stays_a_rename(self) -> None:
        for old_qn, new_qn in self._QUOTED_ONLY:
            outcome = _classify_outcome(
                _identity(old_qn, "", "sig:o\x1fs"),
                _identity(new_qn, "", "sig:n\x1fs"),
            )
            assert outcome == OUTCOME_RENAMED, (old_qn, new_qn, outcome)

    def test_a_real_marker_beside_a_quoted_lookalike_still_counts(self) -> None:
        """The negative control: protecting quoted spans must not blind the
        extraction to a real marker sharing the identity with one."""
        from abicheck.model.graph_identity import closure_marker_locations

        assert closure_marker_locations(
            'Mix<(lambda at real.h:1:2),"lambda:fake.h:9:9">'
        ) == (("lambda", "real.h", "1", "2"),)
        outcome = _classify_outcome(
            _identity(
                'Mix<(lambda at old.h:1:2),"lambda:fake.h:9:9">', "", "sig:x\x1fs"
            ),
            _identity(
                'Mix<(lambda at new.h:3:4),"lambda:fake.h:9:9">', "", "sig:x\x1fs"
            ),
        )
        assert outcome == OUTCOME_MOVED, outcome

    def test_the_two_complements_agree_on_what_is_a_marker(self) -> None:
        """The general statement: whenever the location-free key is
        unchanged, every marker the extraction reports is one the key
        actually stripped — so the two can never disagree about whether a
        span is location evidence."""
        from abicheck.model.graph_identity import (
            closure_location_free_identity,
            closure_marker_locations,
        )

        for identity in (
            'Tag<"lambda:a.h:1:2">',
            "w<(lambda at /a/foo.h:4:37)>",
            'Mix<(lambda at real.h:1:2),"lambda:fake.h:9:9">',
            "Pair<(lambda at a.h:1:2),(lambda:b.h:3:4)>",
            "ns::Widget",
        ):
            stripped = closure_location_free_identity(identity)
            for _kind, basename, _line, _col in closure_marker_locations(identity):
                assert basename not in stripped, (identity, basename, stripped)


class TestReorderedMarkersAreNotAMove:
    """Codex review (PR #1229): with several same-kind markers, a
    *reordering* left the location-free keys equal while the positional
    tuples differed, so a template-argument swap was reported as
    `declaration_moved` even though every marker still names the file it
    already named.

    The invariant is about the multiset: "some marker now names a
    different file" is what a move means, and a permutation does not say
    that. It is still a real difference the location-free key collapses,
    so it is not a coordinate-only shift either."""

    _A, _B = "(lambda at a.h:1:2)", "(lambda at b.h:3:4)"

    def _pair(self, old_qn: str, new_qn: str, decl_file: str = "") -> str:
        return _classify_outcome(
            _identity(old_qn, decl_file, "sig:x\x1fs"),
            _identity(new_qn, decl_file, "sig:x\x1fs"),
        )

    def test_a_permutation_is_not_a_move(self) -> None:
        assert (
            self._pair(f"Pair<{self._A},{self._B}>", f"Pair<{self._B},{self._A}>")
            == OUTCOME_RECONCILED
        )

    def test_a_permutation_is_not_a_coordinate_only_shift_either(self) -> None:
        """Reordered arguments are a real difference the location-free key
        collapses, so the pair must not claim "no material identity
        change" — asserted separately from the move question, since a fix
        that only suppressed the move could land here instead."""
        outcome = self._pair(f"Pair<{self._A},{self._B}>", f"Pair<{self._B},{self._A}>")
        assert outcome != OUTCOME_COORDINATES_ONLY

    def test_a_genuine_marker_move_among_several_is_still_a_move(self) -> None:
        """The must-stay-distinct half: changing one marker's file while
        the others hold is still a move, so the permutation rule cannot
        degrade into "multi-marker names never move"."""
        moved_b = "(lambda at c.h:3:4)"
        assert (
            self._pair(f"Pair<{self._A},{self._B}>", f"Pair<{self._A},{moved_b}>")
            == OUTCOME_MOVED
        )
        # ... including when the move is itself spelled as a reordering of
        # one marker plus a file change in another.
        assert (
            self._pair(f"Pair<{self._A},{self._B}>", f"Pair<{moved_b},{self._A}>")
            == OUTCOME_MOVED
        )

    def test_markers_of_different_kinds_swapping_files_is_a_move(self) -> None:
        """Codex review (PR #1229): the multiset has to carry each marker's
        KIND, not only its basename. A lambda and an unnamed struct that
        each change file leave the bare-basename multiset equal, so the
        permutation rule absorbed a real two-marker move — and then claimed
        the location-free name had changed instead.

        Stated over every pair of distinct kinds the marker vocabulary
        accepts, not the one reported pair, since the defect is "the
        multiset element is too coarse", not "lambda vs unnamed struct"."""
        kinds = ("lambda", "unnamed struct", "anonymous union", "unnamed enum")
        for i, first in enumerate(kinds):
            for second in kinds[i + 1 :]:
                old_qn = f"Pair<({first} at a.h:1:2),({second} at b.h:3:4)>"
                new_qn = f"Pair<({first} at b.h:1:2),({second} at a.h:3:4)>"
                assert self._pair(old_qn, new_qn) == OUTCOME_MOVED, (old_qn, new_qn)

    def test_same_kind_markers_swapping_positions_is_still_not_a_move(self) -> None:
        """The must-stay-distinct complement, over the same kinds: when the
        two markers share a kind there is no recoverable correspondence
        between the sides, which is exactly the case the permutation rule
        exists for — so carrying the kind must not turn every reorder into
        a move."""
        for kind in ("lambda", "unnamed struct", "anonymous union", "unnamed enum"):
            a, b = f"({kind} at a.h:1:2)", f"({kind} at b.h:3:4)"
            assert self._pair(f"Pair<{a},{b}>", f"Pair<{b},{a}>") == OUTCOME_RECONCILED

    def test_markers_from_one_header_swapping_places_is_still_a_permutation(
        self,
    ) -> None:
        """Codex review (PR #1229): two same-kind markers declared in the
        SAME header have identical `(kind, basename)` pairs, so a swap of
        them was invisible and the pair read
        `declaration_coordinates_shifted` — "no material identity change" —
        although it is the same template-argument reorder a differing
        basename already reports.

        The permutation question therefore reads the whole discriminator,
        `line`/`col` included, while the move question keeps reading only
        the file. Checked over every kind, and over 2- and 3-marker names,
        since the defect is "the permutation projection was too coarse"."""
        for kind in ("lambda", "unnamed struct", "anonymous union", "unnamed enum"):
            a = f"({kind} at same.h:1:2)"
            b = f"({kind} at same.h:3:4)"
            c = f"({kind} at same.h:5:6)"
            assert self._pair(f"P<{a},{b}>", f"P<{b},{a}>") == OUTCOME_RECONCILED, kind
            assert (
                self._pair(f"P<{a},{b},{c}>", f"P<{c},{a},{b}>") == OUTCOME_RECONCILED
            ), kind

    def test_coordinate_churn_in_one_header_is_still_not_a_permutation(self) -> None:
        """The must-stay-distinct complement, and the reason the whole
        discriminator is safe to read here: genuine coordinate churn does
        not merely REORDER the discriminators, it changes them, so the
        sorted sequences differ and the pair is still the coordinate-only
        shift this outcome exists for.

        Includes a case sharing one coordinate with the old side, so the
        rule cannot degrade into "any repeated-basename name is a
        permutation"."""
        a, b = "(lambda at same.h:1:2)", "(lambda at same.h:3:4)"
        assert self._pair(
            f"P<{a},{b}>", "P<(lambda at same.h:7:8),(lambda at same.h:9:9)>"
        ) == (OUTCOME_COORDINATES_ONLY)
        assert self._pair(f"P<{a},{b}>", f"P<{a},(lambda at same.h:9:9)>") == (
            OUTCOME_COORDINATES_ONLY
        )

    def test_a_marker_kind_change_at_one_file_is_a_rename_not_a_move(self) -> None:
        """CodeRabbit review (PR #1229): carrying the kind in the multiset
        made a kind-ONLY change read as a move. `X<(lambda at a.h:1:2)>` ->
        `X<(unnamed struct at a.h:1:2)>` names `a.h` throughout, and the
        location-free key keeps the kind, so it is already a rename —
        asserting a move on top of that claims a file change nothing shows.

        Over every ordered pair of kinds, not the one shape: the defect is
        "a differing pair was read as differing FILES", which has nothing
        to do with which two kinds are involved."""
        kinds = ("lambda", "unnamed struct", "anonymous union", "unnamed enum")
        for first in kinds:
            for second in kinds:
                if first == second:
                    continue
                old_qn = f"X<({first} at a.h:1:2)>"
                new_qn = f"X<({second} at a.h:1:2)>"
                assert self._pair(old_qn, new_qn) == OUTCOME_RENAMED, (old_qn, new_qn)

    def test_misaligned_marker_kinds_carry_no_move_evidence_at_all(self) -> None:
        """The same rule when the file changes too: with the kinds no longer
        lining up there is no correspondence between the sides, so nothing
        says WHICH marker moved — the same answer a differing marker COUNT
        already gets (`test_unalignable_marker_counts_make_no_move_claim`). The
        rename is still reported; only the unsupported move
        claim is withheld."""
        assert (
            self._pair("X<(lambda at a.h:1:2)>", "X<(unnamed struct at b.h:1:2)>")
            == OUTCOME_RENAMED
        )

    def test_a_repeated_marker_file_is_compared_as_a_multiset(self) -> None:
        """Two markers naming the same file, one of which moves, must
        still read as a move — a set-based comparison would lose that."""
        assert (
            self._pair(
                f"Pair<{self._A},(lambda at a.h:5:6)>",
                f"Pair<{self._A},(lambda at c.h:5:6)>",
            )
            == OUTCOME_MOVED
        )
        assert (
            self._pair(
                f"Pair<{self._A},(lambda at a.h:5:6)>",
                "Pair<(lambda at a.h:7:8),(lambda at a.h:9:9)>",
            )
            == OUTCOME_COORDINATES_ONLY
        )
