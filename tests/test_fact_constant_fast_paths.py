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

"""``Fact``'s constant fast paths return what a direct construction would.

Every declaration's ``__post_init__`` bridges each legacy field through
``Fact.present(value)`` / ``Fact.not_collected()``, so those calls run
millions of times per large snapshot; the fast paths skip the flyweight's
key construction. Oracle: ``Fact(status=..., value=..., diagnostics=(),
producer=None)`` built directly -- not the helper under test. Checked for
every fast-pathed shape, with the arguments that must fall back to the
general path, and against the one trap an enum key has: two ``StrEnum``
members with one string value compare equal.
"""

from __future__ import annotations

from enum import Enum, StrEnum

import pytest

from abicheck.model import Param, ParamKind
from abicheck.model.availability import FactStatus
from abicheck.model.fact import Fact


class _A(StrEnum):
    X = "x"


class _B(StrEnum):
    X = "x"


class _Plain(Enum):
    ONE = 1


_VALUES = [
    None,
    True,
    False,
    ParamKind.VALUE,
    ParamKind.POINTER,
    _A.X,
    _B.X,
    _Plain.ONE,
]


def _direct(status: FactStatus, value: object) -> Fact[object]:
    return Fact(status=status, value=value, diagnostics=(), producer=None)


@pytest.mark.parametrize("value", _VALUES, ids=repr)
def test_present_constant_equals_direct_construction_and_is_shared(value) -> None:
    got = Fact.present(value)
    assert got == _direct(FactStatus.PRESENT, value)
    assert type(got.value) is type(value)  # True is never served for 1, etc.
    assert Fact.present(value) is got


def test_equal_comparing_str_enums_never_share_a_fact() -> None:
    assert _A.X == _B.X  # the trap: a value-keyed table would collide
    assert Fact.present(_A.X).value is _A.X
    assert Fact.present(_B.X).value is _B.X


@pytest.mark.parametrize(
    ("make", "status"),
    [
        (Fact.not_collected, FactStatus.NOT_COLLECTED),
        (Fact.unsupported, FactStatus.UNSUPPORTED),
        (Fact.not_applicable, FactStatus.NOT_APPLICABLE),
    ],
)
def test_valueless_constants_equal_direct_construction_and_are_shared(
    make, status: FactStatus
) -> None:
    got = make()
    assert got == _direct(status, None)
    assert make() is got
    # With diagnostics or a producer the general path runs and keeps them.
    assert make("why").diagnostics == ("why",)
    assert make(producer="castxml").producer == "castxml"
    assert make("why") != got and make(producer="castxml") != got


@pytest.mark.parametrize("value", [0, 1, "", "x", (), [], 1.0], ids=repr)
def test_non_constant_values_take_the_general_path_unchanged(value) -> None:
    got = Fact.present(value)
    assert got == _direct(FactStatus.PRESENT, value)
    assert type(got.value) is type(value)


def test_present_with_diagnostics_or_producer_is_not_the_constant() -> None:
    assert Fact.present(False, "d").diagnostics == ("d",)
    assert Fact.present(False, producer="clang").producer == "clang"
    assert Fact.present(False, producer="clang") is not Fact.present(False)


def test_declaration_bridge_output_is_unchanged() -> None:
    p = Param(name="a", type="int")
    assert p.is_restrict_fact == _direct(FactStatus.NOT_COLLECTED, None) or (
        p.is_restrict_fact == _direct(FactStatus.PRESENT, False)
    )
    explicit = Param(name="a", type="int", is_restrict=True, kind=ParamKind.POINTER)
    assert explicit.is_restrict_fact == _direct(FactStatus.PRESENT, True)
    assert explicit.kind_fact == _direct(FactStatus.PRESENT, ParamKind.POINTER)
