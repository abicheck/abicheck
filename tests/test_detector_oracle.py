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

"""Deterministic oracle tests for the detector pipeline.

Each case applies one *known* ABI edit (from ``_detector_mutations.MUTATIONS``)
and asserts the ``ChangeKind`` it must produce, the verdict's severity class,
and that an unrelated context symbol is not collaterally flagged.

Unlike ``test_detector_properties.py`` (Hypothesis, ``slow``), these are plain,
deterministic, and **not** ``slow`` — so they run in the default fast lane and,
crucially, fall within ``mutmut``'s test runner. That makes these the assertions
mutation testing measures: a surviving mutant in a detector means an edit it
should have caught went unverified here.

Every case runs in three spellings of the same declarations: as written (fully
qualified), and as the castxml and clang header backends spell them
(``_frontend_spelling.render``, pinned to real dumps by
``test_frontend_spelling_integration.py``). Hand-built snapshots only ever
used the first, so a detector that matched names by their written form passed
here while missing the finding on real clang output (#1918).
"""

from __future__ import annotations

import pytest
from _detector_mutations import (
    ASYMMETRIC,
    CTX_PREFIX,
    MUTATIONS,
    build_snapshot,
    context_identifiers,
)
from _frontend_spelling import FRONTENDS, Frontend, render, split_scope

from abicheck.checker import compare
from abicheck.checker_policy import Verdict
from abicheck.model import Function, RecordType, Visibility

_BREAKING = {Verdict.API_BREAK, Verdict.BREAKING}

SPELLINGS: tuple[Frontend | None, ...] = (None, *FRONTENDS)
_NON_BREAKING = {Verdict.NO_CHANGE, Verdict.COMPATIBLE, Verdict.COMPATIBLE_WITH_RISK}

# A small fixed context exercised in every case: an unrelated public function and
# struct that must remain unflagged by any mutation.
_CONTEXT = {
    "functions": [
        Function(
            name=f"{CTX_PREFIX}keep",
            mangled=f"_Z{CTX_PREFIX}keepv",
            return_type="int",
            visibility=Visibility.PUBLIC,
        ),
    ],
    "types": [RecordType(name=f"{CTX_PREFIX}Keep", kind="struct", size_bits=64)],
}


def _spelling_id(fe: Frontend | None) -> str:
    return "as-written" if fe is None else str(fe)


_spelled = pytest.mark.parametrize("spelling", SPELLINGS, ids=_spelling_id)


def _pair(mutation, tag: int, spelling: Frontend | None):
    """The mutation's old/new snapshots in *spelling*, plus its oracle."""
    old_extra, new_extra, expected_kind, is_breaking = mutation(tag=tag)
    canonical = {
        tuple(pair)
        for extra in (old_extra, new_extra)
        for pair in extra.get("canonical_fields", ())
    }

    def spell(snap):
        return render(snap, spelling, canonical_fields=canonical, leaf_decl_names=True)

    old = spell(build_snapshot("1.0", _CONTEXT, old_extra))
    new = spell(build_snapshot("2.0", _CONTEXT, new_extra))
    return old, new, expected_kind, is_breaking


def _spelled_identifiers(spelling: Frontend | None) -> set[str]:
    """The context's identifiers as *spelling* writes them (a leaf name)."""
    ids = context_identifiers(_CONTEXT)
    if spelling is None:
        return ids
    return ids | {split_scope(i)[-1] for i in ids if i}


@_spelled
@pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda m: m.__name__)
def test_known_mutation_produces_expected_kind(mutation, spelling) -> None:
    old, new, expected_kind, _ = _pair(mutation, 1, spelling)

    emitted = {c.kind for c in compare(old, new).changes}
    assert expected_kind in emitted, (
        f"{mutation.__name__}: expected {expected_kind.name}, "
        f"got {sorted(k.name for k in emitted)}"
    )


@_spelled
@pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda m: m.__name__)
def test_known_mutation_verdict_severity(mutation, spelling) -> None:
    old, new, expected_kind, is_breaking = _pair(mutation, 2, spelling)

    verdict = compare(old, new).verdict
    if is_breaking:
        assert verdict in _BREAKING, (
            f"{expected_kind.name} should be breaking, got {verdict.name}"
        )
    else:
        assert verdict in _NON_BREAKING, (
            f"{expected_kind.name} should be non-breaking, got {verdict.name}"
        )


@_spelled
@pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda m: m.__name__)
def test_known_mutation_does_not_flag_context(mutation, spelling) -> None:
    """False-positive guard: the unchanged context must never be reported."""
    old, new, _, _ = _pair(mutation, 3, spelling)

    flagged = {c.symbol for c in compare(old, new).changes}
    offenders = flagged & _spelled_identifiers(spelling)
    assert not offenders, f"{mutation.__name__} spuriously flagged {offenders}"


@_spelled
@pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda m: m.__name__)
def test_known_mutation_is_direction_symmetric(mutation, spelling) -> None:
    """The edited symbol surfaces in both compare directions.

    Skips mutations whose reverse is legitimately a non-change (see ASYMMETRIC).
    """
    if mutation.__name__ in ASYMMETRIC:
        pytest.skip("reverse edit is ABI-compatible; symmetry intentionally N/A")
    old, new, _, _ = _pair(mutation, 4, spelling)

    forward = {c.symbol for c in compare(old, new).changes}
    backward = {c.symbol for c in compare(new, old).changes}
    assert forward == backward


@pytest.mark.parametrize("spelling", FRONTENDS, ids=str)
@pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda m: m.__name__)
def test_frontend_spelling_does_not_change_the_answer(mutation, spelling) -> None:
    """The same edit spelled by a header frontend yields the same kinds and verdict."""
    base_old, base_new, _, _ = _pair(mutation, 5, None)
    old, new, _, _ = _pair(mutation, 5, spelling)
    base, spelled = compare(base_old, base_new), compare(old, new)
    assert sorted(c.kind.value for c in spelled.changes) == sorted(
        c.kind.value for c in base.changes
    )
    assert spelled.verdict == base.verdict
