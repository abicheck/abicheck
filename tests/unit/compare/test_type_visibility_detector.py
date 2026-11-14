"""TYPE_VISIBILITY_CHANGED: a class's own typeinfo/vtable symbols narrow while
its members stay exported (``compare/type_visibility.py``).

The oracle is the Itanium rule stated independently here: a class's own
symbols are ``_ZT{I,S,V,T}<class>``; its members are ``_ZN[K]<class>...``.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.checker import ChangeKind, compare
from abicheck.compare.type_visibility import type_visibility_changes
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolType
from abicheck.model import AbiSnapshot, RecordType

_CLASSES = {
    "Base": "4Base",
    "ns::Widget": "N2ns6WidgetE",
    "a::b::Deep": "N1a1b4DeepE",
}


def _member(tail: str) -> str:
    inner = tail[1:-1] if tail.startswith("N") else tail
    return f"_ZN{inner}1fEv"


def _snap(
    symbols: list[tuple[str, str]], types: list[RecordType] | None = None
) -> AbiSnapshot:
    s = AbiSnapshot(
        library="libt.so.1", version="1", functions=[], variables=[], types=types or []
    )
    s.elf = ElfMetadata(
        symbols=[
            ElfSymbol(
                name=n,
                visibility=v,
                sym_type=SymbolType.OBJECT if n.startswith("_ZT") else SymbolType.FUNC,
            )
            for n, v in symbols
        ]
    )
    return s


def _family(
    tail: str, vis: str = "default", which: str = "ISV"
) -> list[tuple[str, str]]:
    return [(f"_ZT{c}{tail}", vis) for c in which]


@pytest.mark.parametrize(("name", "tail"), sorted(_CLASSES.items()))
@pytest.mark.parametrize("which", ["ISV", "IS", "V", "ISVT"])
def test_hidden_class_with_exported_members_is_reported(
    name: str, tail: str, which: str
) -> None:
    old = _snap([*_family(tail, which=which), (_member(tail), "default")])
    new = _snap([(_member(tail), "default")])
    (change,) = type_visibility_changes(old, new)
    assert change.kind is ChangeKind.TYPE_VISIBILITY_CHANGED
    assert change.symbol == name
    assert (change.old_value, change.new_value) == ("default", "hidden")
    assert sorted(change.affected_symbols) == sorted(f"_ZT{c}{tail}" for c in which)


@pytest.mark.parametrize("tail", sorted(_CLASSES.values()))
def test_default_to_protected_is_reported(tail: str) -> None:
    old = _snap([*_family(tail), (_member(tail), "default")])
    new = _snap([*_family(tail, "protected"), (_member(tail), "default")])
    (change,) = type_visibility_changes(old, new)
    assert (change.old_value, change.new_value) == ("default", "protected")


@pytest.mark.parametrize(
    ("old_family", "new_family", "old_member", "new_member"),
    [
        # unchanged
        (True, True, True, True),
        # whole class removed: its member removals report themselves
        (True, False, True, False),
        # widening (hidden -> default) adds exports; not this breaking kind
        (False, True, True, True),
        # no member evidence on the old side
        (True, False, False, True),
    ],
)
def test_silent_cases(
    old_family: bool, new_family: bool, old_member: bool, new_member: bool
) -> None:
    for tail in _CLASSES.values():
        old = _snap(
            (_family(tail) if old_family else [])
            + ([(_member(tail), "default")] if old_member else [])
        )
        new = _snap(
            (_family(tail) if new_family else [])
            + ([(_member(tail), "default")] if new_member else [])
        )
        assert type_visibility_changes(old, new) == []


def test_class_that_lost_its_last_virtual_is_not_a_visibility_change() -> None:
    tail = _CLASSES["Base"]
    old = _snap(
        [*_family(tail), (_member(tail), "default")],
        [RecordType(name="Base", kind="class", vtable=["_ZN4Base1fEv"])],
    )
    new = _snap(
        [(_member(tail), "default")], [RecordType(name="Base", kind="class", vtable=[])]
    )
    assert type_visibility_changes(old, new) == []


def test_other_class_members_do_not_count_as_evidence() -> None:
    # "Base" family vanishes; only "Base2" (a different class) keeps members.
    old = _snap([*_family("4Base"), ("_ZN5Base21fEv", "default")])
    new = _snap([("_ZN5Base21fEv", "default")])
    assert type_visibility_changes(old, new) == []


def test_independent_classes_are_judged_independently() -> None:
    tails = sorted(_CLASSES.values())
    for hidden in itertools.chain.from_iterable(
        itertools.combinations(tails, r) for r in range(len(tails) + 1)
    ):
        old = _snap([s for t in tails for s in (*_family(t), (_member(t), "default"))])
        new = _snap(
            [
                s
                for t in tails
                for s in ([] if t in hidden else _family(t)) + [(_member(t), "default")]
            ]
        )
        got = {c.affected_symbols[0][4:] for c in type_visibility_changes(old, new)}
        assert got == set(hidden)


def test_reaches_the_public_compare_result() -> None:
    tail = _CLASSES["ns::Widget"]
    old = _snap([*_family(tail), (_member(tail), "default")])
    new = _snap([(_member(tail), "default")])
    r = compare(old, new)
    assert any(
        c.kind is ChangeKind.TYPE_VISIBILITY_CHANGED and c.symbol == "ns::Widget"
        for c in r.changes
    )
