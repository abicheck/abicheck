# SPDX-License-Identifier: Apache-2.0
"""castxml and clang must model the same fact for the same header.

Regression tests for two normalizations found by the catalog frontend
kind-parity gate (``tests/catalog_kind_parity.py``):

* a clang-parsed builtin-typed variable records the compiler-probed natural
  alignment castxml's computed ``align`` records, through a top-level
  cv-qualifier but never through a typedef or a compound type (case11,
  case39);
* an integer type spelled ``long int`` (DWARF/castxml) and ``long`` (clang)
  is the same transition across evidence tiers (case40).

Each oracle is independent of the code under test: a hand-written
size/alignment table, and the C rule that the redundant ``int`` and specifier
order carry no meaning.
"""

from __future__ import annotations

import itertools
from typing import Any

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.diff_helpers import cross_tier_transition
from abicheck.dumper_clang import _clang_var_alignment_bits
from abicheck.extract.builtin_abi import BuiltinAbi, builtin_type_fact
from abicheck.model.change import Change

#: (size, align) in bytes per builtin: an LP64 target, written out by hand.
_LP64 = {
    "char": (1, 1),
    "short": (2, 2),
    "int": (4, 4),
    "long": (8, 8),
    "unsigned int": (4, 4),
    "unsigned long": (8, 8),
    "double": (8, 8),
    "float": (4, 4),
}
_ABI = BuiltinAbi(plain_char_unsigned=False, layout=_LP64)
_CV = ("{}", "const {}", "volatile {}", "const volatile {}", "{} const")


def _var_node(
    qual: str, *, desugared: str | None = None, aligned: int | None = None
) -> dict[str, Any]:
    typ = {"qualType": qual}
    if desugared is not None:
        typ["desugaredQualType"] = desugared
    inner: list[dict[str, Any]] = []
    if aligned is not None:
        inner.append(
            {
                "kind": "AlignedAttr",
                "inner": [{"kind": "ConstantExpr", "value": str(aligned)}],
            }
        )
    return {"kind": "VarDecl", "type": typ, "inner": inner}


@pytest.mark.parametrize(("builtin", "cv"), list(itertools.product(sorted(_LP64), _CV)))
def test_builtin_variable_records_its_natural_alignment(builtin: str, cv: str) -> None:
    node = _var_node(cv.format(builtin))
    assert _clang_var_alignment_bits(node, _ABI) == _LP64[builtin][1] * 8


def test_explicit_alignment_wins_over_the_natural_one() -> None:
    assert _clang_var_alignment_bits(_var_node("int", aligned=64), _ABI) == 512


@pytest.mark.parametrize(
    "node",
    [
        _var_node("const int *"),
        _var_node("int *const"),
        _var_node("const int &"),
        _var_node("int[4]"),
        _var_node("std::vector<int>"),
        _var_node("struct Foo"),
        # an aligned typedef desugars to the plain builtin: never trusted
        _var_node("al16_t", desugared="int"),
    ],
    ids=["ptr-to-const", "const-ptr", "ref", "array", "template", "record", "typedef"],
)
def test_natural_alignment_is_not_invented(node: dict[str, Any]) -> None:
    assert _clang_var_alignment_bits(node, _ABI) is None


def test_no_probe_means_no_natural_alignment() -> None:
    assert _clang_var_alignment_bits(_var_node("int"), None) is None


def test_builtin_type_fact_still_refuses_a_cv_spelling() -> None:
    """The cv strip is local to the alignment read: a retype's fact
    comparison (``const int`` against ``int``) must stay name-based."""
    assert builtin_type_fact("const int", _ABI) is None


#: Spellings of one type that C treats as identical, per group.
_SAME_TYPE = [
    ("long", "long int", "signed long", "long signed int"),
    ("unsigned long", "long unsigned int", "unsigned long int"),
    ("short", "short int", "signed short"),
    ("long long", "long long int"),
    ("unsigned", "unsigned int"),
]


def _field_change(kind: ChangeKind, old: str, new: str) -> Change:
    return Change(
        kind=kind,
        symbol="Packet::version",
        description="",
        old_value=old,
        new_value=new,
    )


@pytest.mark.parametrize("group", _SAME_TYPE, ids=lambda g: g[0].replace(" ", "_"))
def test_dwarf_and_clang_integer_spellings_agree_across_tiers(
    group: tuple[str, ...],
) -> None:
    for dwarf_new, clang_new in itertools.product(group, repeat=2):
        dwarf = _field_change(ChangeKind.STRUCT_FIELD_TYPE_CHANGED, "int", dwarf_new)
        clang = _field_change(ChangeKind.TYPE_FIELD_TYPE_CHANGED, "int", clang_new)
        assert cross_tier_transition(dwarf) == cross_tier_transition(clang)


@pytest.mark.parametrize(
    ("a", "b"),
    [(x[0], y[0]) for x, y in itertools.combinations(_SAME_TYPE, 2)]
    + [("long", "long *"), ("int", "unsigned int"), ("char", "signed char")],
)
def test_distinct_integer_types_still_disagree(a: str, b: str) -> None:
    dwarf = _field_change(ChangeKind.STRUCT_FIELD_TYPE_CHANGED, "int", a)
    clang = _field_change(ChangeKind.TYPE_FIELD_TYPE_CHANGED, "int", b)
    assert cross_tier_transition(dwarf) != cross_tier_transition(clang)
