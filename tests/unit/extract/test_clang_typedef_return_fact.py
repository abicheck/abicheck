# SPDX-License-Identifier: Apache-2.0
"""A Clang return type spelled through a typedef takes the nearest typedef's fact.

Clang gives a function's own type no desugared spelling, so the return slot's
ABI fact is looked up by name among the translation unit's typedefs. The
lookup follows C++ name lookup: innermost enclosing scope first, and the
nearest *declared* typedef decides, even when its target (a record) has no
fact. An outer same-named typedef of a builtin must never be borrowed: that
would make a real return retype read as ABI-equivalent.

The oracle is the scope list itself (which declaration is nearest), not the
implementation's helper; ``_LP64`` is the hand-written target layout.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from abicheck.extract.builtin_abi import BuiltinAbi
from abicheck.extract.headers.clang.return_type import typedef_return_fact
from abicheck.extract.headers.clang.typedefs import (
    declared_typedef_facts,
    typedef_abi_facts,
)
from abicheck.model import Fact
from abicheck.model.abi_fundamental import AbiFundamental, FundamentalEncoding

_LP64 = {"int": (4, 4), "long": (8, 8), "unsigned int": (4, 4)}
_ABI = BuiltinAbi(plain_char_unsigned=False, layout=_LP64)
_INT = Fact.present(AbiFundamental(FundamentalEncoding.SIGNED, 4, 4))
_LONG = Fact.present(AbiFundamental(FundamentalEncoding.SIGNED, 8, 8))


@dataclass
class _Entry:
    qualified: str
    target: str
    file: str = "/src/api.h"

    @property
    def node(self) -> dict[str, Any]:
        name = self.qualified.rsplit("::", 1)[-1]
        return {"name": name, "type": {"qualType": self.target}}


@dataclass
class _Parser:
    entries: list[_Entry]
    builtin_abi: BuiltinAbi | None = _ABI

    @property
    def _typedefs(self) -> list[_Entry]:
        return self.entries

    def _qualified(self, entry: _Entry) -> str:
        return entry.qualified


_TYPEDEFS = _Parser(
    [
        _Entry("handle_t", "int"),
        _Entry("lib::handle_t", "lib::H"),  # a record: no ABI fact
        _Entry("lib::count_t", "long"),
        _Entry("lib::inner::count_t", "unsigned int"),
        _Entry("size_like", "int", file="<built-in>"),  # never a user typedef
    ]
)


def test_declared_typedefs_keep_the_ones_without_a_fact() -> None:
    declared = declared_typedef_facts(_TYPEDEFS)  # type: ignore[arg-type]
    assert declared == {
        "handle_t": _INT,
        "lib::handle_t": None,
        "lib::count_t": _LONG,
        "lib::inner::count_t": Fact.present(
            AbiFundamental(FundamentalEncoding.UNSIGNED, 4, 4)
        ),
    }
    # typedef_abi_facts, the persisted map, keeps only the ones with a fact.
    assert set(typedef_abi_facts(_TYPEDEFS)) == {  # type: ignore[arg-type]
        "handle_t",
        "lib::count_t",
        "lib::inner::count_t",
    }


@pytest.mark.parametrize(
    ("spelling", "scope", "expected"),
    [
        # The nearest declaration is a record typedef: no fact, never the
        # global int.
        ("handle_t", ("lib",), None),
        ("const handle_t", ("lib",), None),
        # Outside lib only the global typedef is visible.
        ("handle_t", (), _INT),
        ("handle_t", ("other",), _INT),
        ("::handle_t", ("lib",), _INT),
        # Innermost first: lib::inner::count_t shadows lib::count_t.
        (
            "count_t",
            ("lib", "inner"),
            declared_typedef_facts(_TYPEDEFS)["lib::inner::count_t"],
        ),  # type: ignore[arg-type]
        ("count_t", ("lib",), _LONG),
        ("count_t", (), None),
        ("unknown_t", ("lib",), None),
    ],
)
def test_the_nearest_declared_typedef_decides(
    spelling: str, scope: tuple[str, ...], expected: Fact[AbiFundamental] | None
) -> None:
    declared = declared_typedef_facts(_TYPEDEFS)  # type: ignore[arg-type]
    assert typedef_return_fact(None, spelling, scope, declared) == expected


def test_a_builtin_spelling_is_never_overridden() -> None:
    declared = declared_typedef_facts(_TYPEDEFS)  # type: ignore[arg-type]
    assert typedef_return_fact(_LONG, "handle_t", (), declared) == _LONG
    assert typedef_return_fact(None, "handle_t", (), None) is None
    assert typedef_return_fact(None, "handle_t", (), {}) is None
