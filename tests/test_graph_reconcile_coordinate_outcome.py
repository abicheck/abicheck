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
