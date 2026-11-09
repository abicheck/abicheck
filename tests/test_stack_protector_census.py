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

"""The per-unit stack-protector census and the lost-canary qualification.

A lost ``__stack_chk_fail`` is a hardening regression only when the
protector flag weakened. The census each side's DWARF records decides; the
oracle here is a possible-worlds reading written independently of the
implementation: an ``unrecorded`` unit may have been built with any setting,
and a transition is claimed only when it holds in every world.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.change_catalog.registry import Verdict
from abicheck.model.evidence_status import Confidence
from abicheck.model.snapshot import AbiSnapshot
from abicheck.model.stack_protector import (
    StackProtectorTransition,
    compare_stack_protector,
    level_from_producer,
    stack_protector_census,
)
from abicheck.policy.stack_canary_provenance import (
    PROTECTOR_UNCHANGED_RULE,
    qualify_stack_canary_removal,
)

# Hand-written strength order and protected set (not the module's tables).
_ORDER = ["none", "explicit", "basic", "strong", "all"]
_PROTECTED = {"basic", "strong", "all"}
_SWITCH = {
    "none": "-fno-stack-protector",
    "explicit": "-fstack-protector-explicit",
    "basic": "-fstack-protector",
    "strong": "-fstack-protector-strong",
    "all": "-fstack-protector-all",
}
_NOISE = ["-O2", "-g", "-fstack-clash-protection", "-fstack-protector-foo", "-m32"]


@pytest.mark.parametrize("length", [0, 1, 2, 3])
def test_last_switch_wins_over_every_short_command_line(length: int) -> None:
    tokens = list(_SWITCH.values()) + _NOISE[:2]
    for line in itertools.product(tokens, repeat=length):
        producer = " ".join(("GNU C17 13.3.0", *line))
        switches = [t for t in line if t in _SWITCH.values()]
        expected = (
            next(k for k, v in _SWITCH.items() if v == switches[-1])
            if switches
            else "unrecorded"
        )
        assert level_from_producer(producer).value == expected, producer


@pytest.mark.parametrize(
    "producer",
    [None, "", "Ubuntu clang version 18.1.3 (1ubuntu1)", "GNU C17 13.3.0"],
)
def test_a_producer_without_a_protector_switch_is_unrecorded(producer) -> None:
    assert level_from_producer(producer).value == "unrecorded"


def test_census_counts_compiled_and_unreadable_units_and_skips_assembler_units() -> (
    None
):
    units = [
        ("GNU C17 13.3.0 -O2 -fstack-protector-strong", 0x1D),
        ("GNU C17 13.3.0 -O2 -fstack-protector-strong", 0x1D),
        ("GNU AS 2.42", 0x8001),
        ("clang version 18.1.3", 0x8001),  # clang's integrated assembler unit
        ("clang version 18.1.3", 0x21),
        ("GNU C17 13.3.0 -fno-stack-protector", None),
        (None, None),  # a unit whose top DIE could not be read
    ]
    assert stack_protector_census(units) == {"none": 1, "strong": 2, "unrecorded": 2}
    assert stack_protector_census([("GNU AS 2.42", 0x8001), (None, None)]) == {
        "unrecorded": 1
    }
    assert stack_protector_census([("GNU AS 2.42", 0x8001)]) is None
    assert stack_protector_census([]) is None


@pytest.mark.parametrize("outside", [False, True])
def test_code_no_unit_describes_is_an_unrecorded_unit(outside: bool) -> None:
    units = [("GNU C17 13.3.0 -fstack-protector-strong", 0x1D)]
    census = stack_protector_census(units, code_outside_units=outside)
    expected = {"strong": 1, "unrecorded": 1} if outside else {"strong": 1}
    assert census == expected
    unchanged = compare_stack_protector({"strong": 1}, census)
    assert unchanged.value == ("unknown" if outside else "unchanged")
    assert stack_protector_census([], code_outside_units=True) is None


def _worlds(levels: frozenset[str]):
    """Every concrete assignment of the side's unrecorded units."""
    known = levels - {"unrecorded"}
    if "unrecorded" not in levels:
        yield known
        return
    for extra in _ORDER:
        yield known | {extra}


def _oracle(old: frozenset[str] | None, new: frozenset[str] | None) -> str:
    if not old or not new:
        return "unknown"
    rank = _ORDER.index
    pairs = [(o, n) for o in _worlds(old) for n in _worlds(new)]
    if all(min(map(rank, n)) < min(map(rank, o)) for o, n in pairs):
        return "weakened"
    if all(n - _PROTECTED for _o, n in pairs):
        return "partial"
    if "unrecorded" in old | new:
        return "unknown"
    # Units are not matched across builds: any old unit may be any new one, so
    # "no unit weakened" must hold for every (old unit, new unit) pairing.
    if all(rank(nu) >= rank(ou) for ou in old for nu in new):
        return "unchanged"
    return "mixed"


_LEVELS = [*_ORDER, "unrecorded"]
_SUBSETS = [
    frozenset(c)
    for r in range(len(_LEVELS) + 1)
    for c in itertools.combinations(_LEVELS, r)
]


def test_transition_matches_the_possible_worlds_oracle_exhaustively() -> None:
    seen = set()
    for old, new in itertools.product([None, *_SUBSETS], repeat=2):
        old_census = {k: 2 for k in old} if old is not None else None
        new_census = {k: 1 for k in new} if new is not None else None
        got = compare_stack_protector(old_census, new_census).value
        assert got == _oracle(old, new), (old, new)
        seen.add(got)
    assert seen == {t.value for t in StackProtectorTransition}


def _canary_change() -> Change:
    return Change(
        kind=ChangeKind.STACK_CANARY_REMOVED,
        symbol="__stack_chk_fail",
        description="stack canary removed",
        old_value="canary",
        new_value="none",
    )


def _snap(census: dict[str, int] | None) -> AbiSnapshot:
    return AbiSnapshot(library="libx.so", version="1", cu_stack_protector=census)


@pytest.mark.parametrize(
    ("old", "new", "demoted", "confidence"),
    [
        ({"strong": 3}, {"strong": 3}, True, Confidence.HIGH),
        ({"basic": 1}, {"strong": 1, "all": 1}, True, Confidence.HIGH),
        # One unit could have dropped strong -> basic while another stayed basic.
        ({"strong": 1, "basic": 1}, {"basic": 2}, False, Confidence.HIGH),
        ({"strong": 1, "basic": 1}, {"strong": 1, "basic": 1}, False, Confidence.HIGH),
        ({"all": 2}, {"strong": 2}, False, Confidence.HIGH),
        ({"all": 1}, {"none": 1}, False, Confidence.HIGH),
        ({"strong": 1}, {"strong": 1, "explicit": 1}, False, Confidence.HIGH),
        ({"strong": 1}, {"strong": 1, "unrecorded": 1}, False, Confidence.MEDIUM),
        (None, {"strong": 1}, False, Confidence.MEDIUM),
        ({"strong": 1}, None, False, Confidence.MEDIUM),
        (None, None, False, Confidence.MEDIUM),
    ],
)
def test_a_lost_canary_is_demoted_only_when_every_unit_keeps_the_protector(
    old, new, demoted, confidence
) -> None:
    change = _canary_change()
    other = Change(kind=ChangeKind.RELRO_WEAKENED, symbol="GNU_RELRO", description="")
    qualify_stack_canary_removal([change, other], _snap(old), _snap(new))
    assert other.effective_verdict is None and other.review_evidence is None
    expected = compare_stack_protector(old, new).value
    assert change.review_evidence is not None
    assert change.review_evidence["stack_protector"] == expected
    assert change.review_evidence["old_units"] == old
    assert change.review_evidence["new_units"] == new
    assert change.confidence is confidence
    if demoted:
        assert change.effective_verdict is Verdict.COMPATIBLE
        assert change.modulation_rule == PROTECTOR_UNCHANGED_RULE
        assert "note" not in change.review_evidence
    else:
        assert change.effective_verdict is None and change.modulation_rule is None
        assert change.review_evidence["note"]
    if expected == "unknown":
        assert "weaker evidence" in str(change.review_evidence["note"])


def test_a_prior_modulation_is_left_alone() -> None:
    change = _canary_change()
    change.effective_verdict = Verdict.BREAKING
    change.modulation_rule = "security-policy"
    qualify_stack_canary_removal([change], _snap({"strong": 1}), _snap({"strong": 1}))
    assert change.effective_verdict is Verdict.BREAKING
    assert change.modulation_rule == "security-policy"
    assert change.review_evidence is None
