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

"""``RecordType.source_header_fact`` (ADR-063 Phase 5) staying in sync
across ``tu_merge.py``'s provenance-comparison paths, split out of
``test_tu_merge.py`` (mechanical extraction, unchanged test bodies) once
that file crossed the ADR-061 architecture debt ledger's no-growth
baseline.

Two real bugs Codex's review of PR #982 found, both the same class as
the fixes in ``test_provenance.py``/``test_lambda_identity_ordinal.py``:
a post-construction ``dataclasses.replace()`` that touches the legacy
``source_header`` field without also updating its ``Fact[...]`` sibling,
so ``__post_init__``'s "explicit Fact wins" rule reasserts the stale
value.
"""

from __future__ import annotations

from abicheck.model import RecordType, TypeField
from abicheck.tu_fragment import TuFragment
from abicheck.tu_merge import merge_fragments


def test_merge_fragments_type_two_definitions_with_differing_source_header_merges():
    # ADR-063 Phase 5 (Codex review, P1): RecordType.source_header now
    # carries a Fact[str | None] sibling. _blank_provenance blanks
    # source_header to None for this equality check, but a bare
    # dataclasses.replace() left the pre-blank source_header_fact carried
    # forward unchanged -- __post_init__'s "explicit Fact wins" rule then
    # read the two (genuinely differing, since each side's real
    # source_header sets Fact.present(<that header>)) facts as still
    # disagreeing even after the legacy field was blanked, so this
    # otherwise-routine cross-TU redeclaration spuriously raised
    # INCONSISTENT_DECLARATION instead of merging.
    a_side = RecordType(
        name="X",
        kind="struct",
        fields=[TypeField(name="a", type="int")],
        source_header="a.h",
    )
    b_side = RecordType(
        name="X",
        kind="struct",
        fields=[TypeField(name="a", type="int")],
        source_header="b.h",
    )
    a = TuFragment(tu_name="a", types=(a_side,))
    b = TuFragment(tu_name="b", types=(b_side,))
    merged = merge_fragments([a, b])
    assert len(merged.types) == 1
    winner = merged.types[0]
    # The two representations must agree on the merged winner too --
    # whichever source_header value it kept, its own source_header_fact
    # must describe that same value, not a stale one from either side.
    assert winner.source_header_fact.value == winner.source_header


def test_merge_fragments_type_forward_decl_source_header_fact_stays_synced():
    # ADR-063 Phase 5 (Codex review, P1): _with_more_public_provenance's
    # replace() call carries a new source_header value from the winning
    # side's provenance without also updating source_header_fact -- the
    # merged type's two representations must not disagree.
    public_forward = RecordType(
        name="Point",
        kind="struct",
        is_opaque=True,
        source_location="include/api.h:1",
        source_header="include/api.h",
    )
    private_definition = RecordType(
        name="Point",
        kind="struct",
        fields=[TypeField(name="x", type="int")],
        source_location="internal/detail.h:9",
        source_header="internal/detail.h",
    )
    a = TuFragment(tu_name="a", types=(public_forward,))
    b = TuFragment(tu_name="b", types=(private_definition,))
    merged = merge_fragments(
        [a, b], public_header_paths=["include/api.h"], public_header_dirs=[]
    )
    assert len(merged.types) == 1
    winner = merged.types[0]
    assert winner.source_header == "include/api.h"
    assert winner.source_header_fact.value == "include/api.h"
