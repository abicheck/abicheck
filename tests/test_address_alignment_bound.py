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

"""An address-derived alignment drop is a reduction only against a declared
requirement.

The largest power of two dividing ``st_value`` bounds an object's declared
alignment from above, so a smaller bound on the new side proves a reduction
only when the old side declared a requirement the new placement fails. The
oracle enumerates every declared requirement (none, 1..32 bytes) against
every placement pair and states that rule directly; every demotion keeps the
finding with its rule (ADR-067).
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.change_catalog.registry import Verdict
from abicheck.model.declarations import Variable
from abicheck.model.fact import Fact
from abicheck.model.snapshot import AbiSnapshot
from abicheck.policy.address_alignment_bound import (
    DECLARED_STILL_MET_RULE,
    UNDECLARED_RULE,
    UNKNOWN_DECLARATION_RULE,
    bound_address_alignment_reductions,
)

_SYM = "g_counter"
_POW2 = [1, 2, 4, 8, 16, 32]


def _snap(declared_bytes: int | None) -> AbiSnapshot:
    snap = AbiSnapshot(library="libx.so", version="1")
    var = Variable(
        name=_SYM,
        mangled=_SYM,
        type="int",
        alignment_bits=None if declared_bytes is None else declared_bytes * 8,
    )
    snap.declarations.variables.append(var)
    return snap


def _change(old: int, new: int) -> Change:
    return Change(
        kind=ChangeKind.EXPORTED_OBJECT_ALIGNMENT_REDUCED,
        symbol=_SYM,
        description="alignment reduced",
        old_value=str(old),
        new_value=str(new),
    )


_CELLS = [
    (declared, old, new)
    for declared in [None, *_POW2]
    for old, new in itertools.combinations(sorted(_POW2, reverse=True), 2)
    # A linker never places an object below its declared alignment.
    if declared is None or old >= declared
]


@pytest.mark.parametrize(("declared", "old", "new"), _CELLS)
def test_a_reduction_stands_only_below_the_old_declared_requirement(
    declared, old, new
) -> None:
    change = _change(old, new)
    bound_address_alignment_reductions([change], _snap(declared))
    # The finding is never removed; only its effective verdict is qualified.
    assert change.kind is ChangeKind.EXPORTED_OBJECT_ALIGNMENT_REDUCED
    if declared is not None and new < declared:
        assert change.effective_verdict is None and change.modulation_rule is None
    elif declared is None:
        assert change.effective_verdict is Verdict.COMPATIBLE
        assert change.modulation_rule == UNDECLARED_RULE
    else:
        assert change.effective_verdict is Verdict.COMPATIBLE
        assert change.modulation_rule == DECLARED_STILL_MET_RULE


def test_an_undeclared_object_that_the_old_side_does_not_list_is_demoted() -> None:
    change = _change(16, 8)
    bound_address_alignment_reductions(
        [change], AbiSnapshot(library="libx.so", version="1")
    )
    assert change.modulation_rule == UNDECLARED_RULE


def test_other_findings_and_prior_modulations_are_left_alone() -> None:
    prior = _change(16, 8)
    prior.effective_verdict = Verdict.BREAKING
    prior.modulation_rule = "user-policy"
    other = Change(
        kind=ChangeKind.VAR_ALIGNMENT_CHANGED,
        symbol=_SYM,
        description="",
        old_value="128",
        new_value="64",
    )
    unparseable = _change(16, 8)
    unparseable.new_value = "?"
    bound_address_alignment_reductions([prior, other, unparseable], _snap(None))
    assert prior.effective_verdict is Verdict.BREAKING
    assert prior.modulation_rule == "user-policy"
    assert other.effective_verdict is None
    assert unparseable.effective_verdict is None


@pytest.mark.parametrize(
    ("declared", "old", "new"), [c for c in _CELLS if c[0] is not None]
)
def test_a_lower_bound_never_stands_in_for_a_declared_requirement(
    declared, old, new
) -> None:
    """A PARTIAL alignment (castxml's natural alignment where a specifier can
    apply, a DWARF default GCC may have written for a definition-only
    specifier) is a lower bound: it neither keeps nor vetoes the reduction,
    and the demotion says the requirement is unknown, not absent."""
    snap = _snap(None)
    var = snap.declarations.variables[0]
    var.alignment_bits = declared * 8
    var.alignment_bits_fact = Fact.partial(declared * 8, "lower bound", producer="t")
    change = _change(old, new)
    bound_address_alignment_reductions([change], snap)
    assert change.kind is ChangeKind.EXPORTED_OBJECT_ALIGNMENT_REDUCED
    assert change.effective_verdict is Verdict.COMPATIBLE
    assert change.modulation_rule == UNKNOWN_DECLARATION_RULE
