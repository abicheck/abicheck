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

from abicheck.checker import compare
from abicheck.checker_policy import Verdict
from abicheck.model import Function, RecordType, Visibility

_BREAKING = {Verdict.API_BREAK, Verdict.BREAKING}
_NON_BREAKING = {Verdict.NO_CHANGE, Verdict.COMPATIBLE, Verdict.COMPATIBLE_WITH_RISK}

# A small fixed context exercised in every case: an unrelated public function and
# struct that must remain unflagged by any mutation.
_CONTEXT = {
    "functions": [
        Function(name=f"{CTX_PREFIX}keep", mangled=f"_Z{CTX_PREFIX}keepv",
                 return_type="int", visibility=Visibility.PUBLIC),
    ],
    "types": [RecordType(name=f"{CTX_PREFIX}Keep", kind="struct", size_bits=64)],
}


@pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda m: m.__name__)
def test_known_mutation_produces_expected_kind(mutation) -> None:
    old_extra, new_extra, expected_kind, _ = mutation(tag=1)
    old = build_snapshot("1.0", _CONTEXT, old_extra)
    new = build_snapshot("2.0", _CONTEXT, new_extra)

    emitted = {c.kind for c in compare(old, new).changes}
    assert expected_kind in emitted, (
        f"{mutation.__name__}: expected {expected_kind.name}, "
        f"got {sorted(k.name for k in emitted)}"
    )


@pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda m: m.__name__)
def test_known_mutation_verdict_severity(mutation) -> None:
    old_extra, new_extra, expected_kind, is_breaking = mutation(tag=2)
    old = build_snapshot("1.0", _CONTEXT, old_extra)
    new = build_snapshot("2.0", _CONTEXT, new_extra)

    verdict = compare(old, new).verdict
    if is_breaking:
        assert verdict in _BREAKING, (
            f"{expected_kind.name} should be breaking, got {verdict.name}"
        )
    else:
        assert verdict in _NON_BREAKING, (
            f"{expected_kind.name} should be non-breaking, got {verdict.name}"
        )


@pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda m: m.__name__)
def test_known_mutation_does_not_flag_context(mutation) -> None:
    """False-positive guard: the unchanged context must never be reported."""
    old_extra, new_extra, _, _ = mutation(tag=3)
    old = build_snapshot("1.0", _CONTEXT, old_extra)
    new = build_snapshot("2.0", _CONTEXT, new_extra)

    flagged = {c.symbol for c in compare(old, new).changes}
    offenders = flagged & context_identifiers(_CONTEXT)
    assert not offenders, f"{mutation.__name__} spuriously flagged {offenders}"


@pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda m: m.__name__)
def test_known_mutation_is_direction_symmetric(mutation) -> None:
    """The edited symbol surfaces in both compare directions.

    Skips mutations whose reverse is legitimately a non-change (see ASYMMETRIC).
    """
    if mutation.__name__ in ASYMMETRIC:
        pytest.skip("reverse edit is ABI-compatible; symmetry intentionally N/A")
    old_extra, new_extra, _, _ = mutation(tag=4)
    old = build_snapshot("1.0", _CONTEXT, old_extra)
    new = build_snapshot("2.0", _CONTEXT, new_extra)

    forward = {c.symbol for c in compare(old, new).changes}
    backward = {c.symbol for c in compare(new, old).changes}
    assert forward == backward
