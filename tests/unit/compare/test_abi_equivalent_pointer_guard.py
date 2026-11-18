"""The pointer guard of fact-based retype equivalence, as a class.

A pointer, reference, pointer-to-function or pointer-to-array slot carries an
ABI fact (one unsigned word) only so that the return-convention and packing
checks can read it. Two such slots are never ABI-equivalent *by fact*: the
fact is size-only and cannot prove the pointees equivalent. So the gate that
every fact-based retype comparison goes through (params, return, field,
variable, typedef) must refuse them even when both facts are equal and
present, and must allow direct scalar slots with the same facts.

The oracle is the spelling a C++ author writes, not the implementation.
"""

from __future__ import annotations

import pytest

from abicheck.compare.abi_equivalent_retype import _comparable_equal, _is_indirect
from abicheck.compare.fact_comparison import compare_facts
from abicheck.model.abi_fundamental import AbiFundamental, FundamentalEncoding
from abicheck.model.fact import Fact

_WORD = AbiFundamental(FundamentalEncoding.UNSIGNED, 8, 8)
_DEFAULT = AbiFundamental(FundamentalEncoding.UNSIGNED, 8, 8)

_INDIRECT = [
    "int *",
    "int*",
    "const char *",
    "struct S *",
    "int *const",
    "int &",
    "int &&",
    "void (*)(int)",
    "int (*const)(int)",
    "int (*)[3]",
    "int (&)[3]",
]
_DIRECT = ["long", "unsigned long", "int", "double", "struct S", "bool"]


def _equal_present_facts() -> object:
    return compare_facts(Fact.present(_WORD), Fact.present(_WORD), _DEFAULT)


@pytest.mark.parametrize("spelling", _INDIRECT)
def test_an_indirect_slot_is_indirect(spelling: str) -> None:
    assert _is_indirect(spelling) is True


@pytest.mark.parametrize("spelling", _DIRECT)
def test_a_direct_slot_is_not_indirect(spelling: str) -> None:
    assert _is_indirect(spelling) is False


@pytest.mark.parametrize("spelling", _INDIRECT)
def test_equal_facts_never_make_an_indirect_pair_equivalent(spelling: str) -> None:
    # Old and new both indirect: the facts are equal and present, but the gate
    # must still refuse. This is the class the regression exposed.
    assert _comparable_equal(_equal_present_facts(), spelling, spelling) is False


@pytest.mark.parametrize("spelling", _INDIRECT)
def test_one_indirect_side_is_enough_to_refuse(spelling: str) -> None:
    assert _comparable_equal(_equal_present_facts(), spelling, "long") is False
    assert _comparable_equal(_equal_present_facts(), "long", spelling) is False


@pytest.mark.parametrize("spelling", _DIRECT)
def test_equal_facts_on_direct_slots_are_equivalent(spelling: str) -> None:
    assert _comparable_equal(_equal_present_facts(), spelling, spelling) is True
