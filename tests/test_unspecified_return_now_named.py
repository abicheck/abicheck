"""``detect_unspecified_return_now_named``: an unspecified return type
(deduced placeholder or unnamed type) that became a named one, and only that
direction (``COMPATIBLE``; ADR-084 2026-11-14 amendment)."""

from __future__ import annotations

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.diff_templates import detect_unspecified_return_now_named
from abicheck.model import AbiSnapshot, Function, Visibility


def _snap(funcs: list[Function]) -> AbiSnapshot:
    return AbiSnapshot(library="libt.so", version="0", functions=funcs)


def _fn(name: str, return_type: str) -> Function:
    return Function(
        name=name,
        mangled=f"_Z{name}",
        return_type=return_type,
        params=[],
        visibility=Visibility.PUBLIC,
    )


class TestUnspecifiedReturnNowNamed:
    def test_auto_to_named(self) -> None:
        old = _snap([_fn("lib::make", return_type="auto")])
        new = _snap([_fn("lib::make", return_type="lib::Foo")])
        changes = detect_unspecified_return_now_named(old, new)
        assert len(changes) == 1
        c = changes[0]
        assert c.kind == ChangeKind.UNSPECIFIED_RETURN_NOW_NAMED
        assert c.old_value == "auto"
        assert c.new_value == "lib::Foo"

    # The oracle is the C++ rule, not the detector's marker list: a caller
    # can name the result type only when it is a named type. A placeholder
    # (deduced ``auto``, ``decltype(...)``) or an unnamed type (a lambda
    # closure, an unnamed class) as each producer spells it can only be held
    # in ``auto``. ``auto`` is a keyword, so ``std::auto_ptr`` is a name.
    _UNSPECIFIED = (
        "auto",
        "decltype(auto)",
        "decltype(lib::g())",
        "lib::make()::<lambda(int)>",  # GCC plugin
        "{lambda(int)#1}",  # DWARF / demangler
        "(lambda:lib.h#1)",  # clang AST
        "(lambda:lib.h:3:10)",
        "lib::make::(unnamed class)",  # castxml closure
        "(unnamed struct:lib.h:2:1)",
        "(anonymous union)",
    )
    _NAMED = (
        "lib::Foo",
        "int",
        "std::auto_ptr<int>",
        "lib::automaton",
        "const lib::Foo&",
        "lib::Box<lib::Foo>",
    )

    @pytest.mark.parametrize("old_rt", _UNSPECIFIED)
    @pytest.mark.parametrize("new_rt", _NAMED)
    def test_every_unspecified_to_named_pair_is_reported(
        self, old_rt: str, new_rt: str
    ) -> None:
        old = _snap([_fn("lib::make", return_type=old_rt)])
        new = _snap([_fn("lib::make", return_type=new_rt)])
        changes = detect_unspecified_return_now_named(old, new)
        assert [(c.kind, c.old_value, c.new_value) for c in changes] == [
            (ChangeKind.UNSPECIFIED_RETURN_NOW_NAMED, old_rt, new_rt)
        ]

    @pytest.mark.parametrize("old_rt", _NAMED)
    @pytest.mark.parametrize("new_rt", _UNSPECIFIED)
    def test_named_to_unspecified_is_not_this_kind(
        self, old_rt: str, new_rt: str
    ) -> None:
        # The reverse breaks callers that spell the type; it is a return-type
        # change (func_return_changed), never "now named".
        old = _snap([_fn("lib::make", return_type=old_rt)])
        new = _snap([_fn("lib::make", return_type=new_rt)])
        assert detect_unspecified_return_now_named(old, new) == []

    @pytest.mark.parametrize(
        ("old_rt", "new_rt"),
        [
            ("auto", "decltype(auto)"),
            ("(lambda:lib.h#1)", "(lambda:lib.h#2)"),
            ("lib::Foo", "lib::Bar"),
            # unresolved spellings (bare or composite) are no evidence
            ("auto", "?"),
            ("auto", "?*"),
            ("auto", "const ?"),
            ("auto", "?&"),
        ],
    )
    def test_no_unspecified_to_named_transition(self, old_rt: str, new_rt: str) -> None:
        old = _snap([_fn("lib::make", return_type=old_rt)])
        new = _snap([_fn("lib::make", return_type=new_rt)])
        assert detect_unspecified_return_now_named(old, new) == []

    def test_kind_is_compatible(self) -> None:
        from abicheck.checker_policy import COMPATIBLE_KINDS

        assert ChangeKind.UNSPECIFIED_RETURN_NOW_NAMED in COMPATIBLE_KINDS

    def test_stable_no_finding(self) -> None:
        old = _snap([_fn("lib::make", return_type="lib::Foo")])
        new = _snap([_fn("lib::make", return_type="lib::Foo")])
        assert detect_unspecified_return_now_named(old, new) == []
