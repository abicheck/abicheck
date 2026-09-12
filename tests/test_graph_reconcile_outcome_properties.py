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

"""``graph_reconcile_outcome.classify``'s claim must be entailed by its
inputs.

Bug class ``classification.default_branch_asserts_more_than_inputs``: a
classifier whose final ``return`` is a fall-through to the strongest
outcome in its vocabulary. ``classify`` ended with an unconditional
``OUTCOME_RECONCILED`` -- prose "both the name and the declaring-file
evidence changed together", severity ``risk`` -- for every pair that was
neither renamed, nor moved, nor coordinate-only. Measured on a oneTBB
header graph, **all 234** reconciled calls were ``source_decl`` pairs
whose ``qualified_name``, ``source_relative`` and two-sided
``declaring_file`` were byte-identical, so the claim was false for the
entire population and 13 of the emitted findings had ``old_value ==
new_value``. PR #1232 had already corrected the *prose* for that case
once; prose is not where the defect lives.

Stated as a property over generated identity pairs in the style
`AGENTS.md` prescribes for a reusable primitive
(``TestPairedStableIndicesProperties`` is the named model), not as a fixed
example built from the ``internal_forward`` pair that happened to be
reported: what the classifier returns must be *entailed* by what it was
given, for every pair in the space, and an unclassifiable pair must not
be answered with the strongest available claim.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.buildsource.entity_identity import CanonicalIdentity
from abicheck.buildsource.graph_reconcile_outcome import (
    _OUTCOME_PROSE,
    OUTCOME_COORDINATES_ONLY,
    OUTCOME_IDENTITY_UNCHANGED,
    OUTCOME_MOVED,
    OUTCOME_RECONCILED,
    OUTCOME_RECONCILED_UNRESOLVED,
    OUTCOME_RENAMED,
    classify,
)

#: Every outcome the classifier can return. Kept as a literal here rather
#: than derived from the module, so adding a sixth outcome without deciding
#: what it claims fails these tests instead of being swept into them.
ALL_OUTCOMES = frozenset(
    {
        OUTCOME_RENAMED,
        OUTCOME_MOVED,
        OUTCOME_RECONCILED,
        OUTCOME_COORDINATES_ONLY,
        OUTCOME_IDENTITY_UNCHANGED,
        OUTCOME_RECONCILED_UNRESOLVED,
    }
)

#: Outcomes whose prose asserts the declaration's NAME changed.
CLAIMS_NAME_CHANGE = frozenset({OUTCOME_RENAMED, OUTCOME_RECONCILED})
#: Outcomes whose prose asserts the declaration's LOCATION changed.
CLAIMS_LOCATION_CHANGE = frozenset({OUTCOME_MOVED, OUTCOME_RECONCILED})


def _identity(
    *,
    qualified_name: str,
    source_relative: str,
    kind: str,
    signature_tail: str = "kind\x1farity0",
) -> CanonicalIdentity:
    """A ``CanonicalIdentity`` shaped the way production builds one.

    ``normalized_signature`` is ``"sig:" + qn + "\\x1f" + tail`` -- the
    classifier strips the first field, so the tail is what it compares.
    Building it here rather than reusing the production constructor keeps
    the oracle independent of the implementation, per `AGENTS.md`'s rule
    that the oracle must not be the same helper the implementation uses.
    """
    return CanonicalIdentity(
        primary_id=f"id:{qualified_name}:{source_relative}",
        tier="canonical",
        qualified_name=qualified_name,
        normalized_signature=f"sig:{qualified_name}\x1f{signature_tail}",
        source_relative=source_relative,
        kind=kind,
    )


#: A small, fully-enumerated input space. Each axis is varied
#: independently, so the product covers every combination of "the name
#: agrees / differs only in coordinates / differs materially", "the
#: declaring file agrees / differs / is absent", both kind families, and
#: agreeing vs. differing signature tails -- 3 x 4 x 2 x 2 = 96 pairs, all
#: of them checked against the entailment oracle below rather than against
#: a recorded expectation per case.
_OLD_NAME = "tbb::detail::d1::internal_forward"
_NAMES = {
    "identical": (_OLD_NAME, _OLD_NAME),
    "coordinates_only": (
        f"{_OLD_NAME}<(lambda at flow_graph.h:172:22)>",
        f"{_OLD_NAME}<(lambda at flow_graph.h:305:9)>",
    ),
    "materially_renamed": (_OLD_NAME, "tbb::detail::d1::internal_forward_v2"),
}
_FILES = {
    "identical": (
        "include/oneapi/tbb/flow_graph.h",
        "include/oneapi/tbb/flow_graph.h",
    ),
    "differing": (
        "include/oneapi/tbb/flow_graph.h",
        "include/oneapi/tbb/detail/_flow_graph_body.h",
    ),
    "absent_both": ("", ""),
    "absent_one": ("include/oneapi/tbb/flow_graph.h", ""),
}
_KINDS = ("source_decl", "record_type")
_TAILS = {
    "same": ("kind\x1farity0", "kind\x1farity0"),
    "differing": ("kind\x1farity0", "kind\x1farity2"),
}

_CASES = [
    (names, files, kind, tails)
    for names, files, kind, tails in itertools.product(
        sorted(_NAMES), sorted(_FILES), _KINDS, sorted(_TAILS)
    )
]


def _pair(names: str, files: str, kind: str, tails: str):
    old_qn, new_qn = _NAMES[names]
    old_file, new_file = _FILES[files]
    old_tail, new_tail = _TAILS[tails]
    old = _identity(
        qualified_name=old_qn,
        source_relative=old_file,
        kind=kind,
        signature_tail=old_tail,
    )
    new = _identity(
        qualified_name=new_qn,
        source_relative=new_file,
        kind=kind,
        signature_tail=new_tail,
    )
    return old, new


@pytest.mark.parametrize("names,files,kind,tails", _CASES)
def test_outcome_is_one_of_the_declared_vocabulary(
    names: str, files: str, kind: str, tails: str
) -> None:
    """No pair may produce an outcome with no declared meaning, and every
    outcome returned must have prose a report can render."""
    old, new = _pair(names, files, kind, tails)
    result = classify(old, new)
    assert result.outcome in ALL_OUTCOMES, result.outcome
    assert result.outcome in _OUTCOME_PROSE, result.outcome


@pytest.mark.parametrize("names,files,kind,tails", _CASES)
def test_a_claim_of_change_is_entailed_by_the_inputs(
    names: str, files: str, kind: str, tails: str
) -> None:
    """The property this class exists for.

    An outcome whose prose asserts the name changed may only be returned
    for a pair whose qualified names actually differ; one asserting the
    location changed may only be returned where the two sides' declaring
    files actually differ. The oracle is the raw input comparison, not the
    classifier's own normalization helpers -- reusing those would let the
    same bug satisfy both sides.
    """
    old, new = _pair(names, files, kind, tails)
    result = classify(old, new)

    if result.outcome in CLAIMS_NAME_CHANGE:
        assert old.qualified_name != new.qualified_name, (
            f"{result.outcome} claims a name change for identical names"
        )
    if result.outcome in CLAIMS_LOCATION_CHANGE:
        assert old.source_relative != new.source_relative, (
            f"{result.outcome} claims a location change for "
            f"identical declaring files"
        )


@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("tail", sorted(_TAILS))
def test_a_wholly_identical_pair_never_claims_a_change(kind: str, tail: str) -> None:
    """The measured population, generalized.

    A pair with equal ``qualified_name``, equal ``source_relative`` and
    equal two-sided ``declaring_file`` -- what all 234 oneTBB reconciled
    calls were -- must never produce an outcome whose prose asserts a name
    or location change, for any kind.

    Which no-claim outcome it gets depends on whether the classifier's
    dimensions are exhaustive for that kind. For a type they are, so an
    agreeing signature tail means genuinely unchanged. For a
    ``source_decl`` they are not -- a pair only reaches reconciliation
    because its node identities differed, so something the classifier
    cannot read did change -- and the honest answer is the unresolved
    outcome, not "unchanged".
    """
    old, new = _pair("identical", "identical", kind, tail)
    result = classify(
        old,
        new,
        old_declaring_file="include/oneapi/tbb/flow_graph.h",
        new_declaring_file="include/oneapi/tbb/flow_graph.h",
    )
    assert result.outcome not in CLAIMS_NAME_CHANGE
    assert result.outcome not in CLAIMS_LOCATION_CHANGE
    assert result.move_established is False
    if tail == "same" and kind != "source_decl":
        assert result.outcome == OUTCOME_IDENTITY_UNCHANGED
    else:
        assert result.outcome == OUTCOME_RECONCILED_UNRESOLVED


@pytest.mark.parametrize("names,files,kind,tails", _CASES)
def test_identity_unchanged_is_returned_only_when_nothing_differs(
    names: str, files: str, kind: str, tails: str
) -> None:
    """The converse guard, so the fifth outcome cannot become the new
    else-bucket: it may be returned only for a pair where every dimension
    the classifier reads actually agrees."""
    old, new = _pair(names, files, kind, tails)
    result = classify(old, new)
    if result.outcome == OUTCOME_IDENTITY_UNCHANGED:
        assert old.qualified_name == new.qualified_name
        assert old.source_relative == new.source_relative
        assert (
            old.normalized_signature.split("\x1f", 1)[-1]
            == new.normalized_signature.split("\x1f", 1)[-1]
        )
        # And only where the dimensions above are the whole identity.
        assert old.kind != "source_decl"


@pytest.mark.parametrize("names,files,kind,tails", _CASES)
def test_classification_is_symmetric_in_the_dimensions_it_reads(
    names: str, files: str, kind: str, tails: str
) -> None:
    """Swapping the two sides may not change *whether* a change is
    claimed. A classifier that answered differently by direction would be
    reading something other than the difference between the sides."""
    old, new = _pair(names, files, kind, tails)
    forward = classify(old, new)
    backward = classify(new, old)
    assert (forward.outcome in CLAIMS_NAME_CHANGE) == (
        backward.outcome in CLAIMS_NAME_CHANGE
    )
    assert (forward.outcome in CLAIMS_LOCATION_CHANGE) == (
        backward.outcome in CLAIMS_LOCATION_CHANGE
    )


def test_the_measured_onetbb_shape_lands_in_the_new_outcome() -> None:
    """The reported instance, kept alongside the property rather than
    instead of it: the exact ``internal_forward`` pair the oneTBB run
    produced 234 of.

    It is a ``source_decl``, so the outcome is the unresolved one, not
    "unchanged": the pair reached reconciliation because its node
    identities differed, and for a function the classifier's dimensions
    do not cover everything that could have. What the split fixes for
    this population is the false claim (``declaration_identity_
    reconciled``'s "both the qualified name and the declaring-file
    evidence changed together"), not the RISK tier -- an unattributable
    difference is weaker evidence, which narrows the conclusion rather
    than licensing a clean one.
    """
    identity = _identity(
        qualified_name="tbb::detail::d1::internal_forward",
        source_relative="include/oneapi/tbb/flow_graph.h",
        kind="source_decl",
    )
    result = classify(
        identity,
        identity,
        old_declaring_file="include/oneapi/tbb/flow_graph.h",
        new_declaring_file="include/oneapi/tbb/flow_graph.h",
    )
    assert result.outcome == OUTCOME_RECONCILED_UNRESOLVED
    assert result.move_established is False


def test_every_outcome_in_the_vocabulary_is_reachable() -> None:
    """Guard against a narrowing that makes an outcome dead.

    ``OUTCOME_RECONCILED`` in particular must stay reachable: narrowing it
    to "both dimensions genuinely changed" is the point of this change,
    but narrowing it out of existence would mean a real rename-and-move
    had nowhere to go.
    """
    reached = {classify(*_pair(n, f, k, t)).outcome for n, f, k, t in _CASES}
    assert OUTCOME_RENAMED in reached
    assert OUTCOME_RECONCILED_UNRESOLVED in reached
    assert OUTCOME_MOVED in reached
    assert OUTCOME_RECONCILED in reached
    assert OUTCOME_COORDINATES_ONLY in reached
    assert OUTCOME_IDENTITY_UNCHANGED in reached
