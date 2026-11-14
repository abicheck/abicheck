"""Covariant return narrowing and override slot reuse (Itanium C++ ABI 2.5).

Two rules, each checked on several independent shapes against an oracle
written from the ABI, not from the implementation:

* A pointer/reference return retyped from ``B`` to ``D`` hands old callers
  the same address exactly when every step from ``D`` to ``B`` is a
  non-virtual base at offset 0. Then the change is not a break
  (``FUNC_RETURN_COVARIANT_NARROWED``); any other derivation, an unknown
  offset, a cv change or a widening keeps ``FUNC_RETURN_CHANGED``.
* A same-signature override of an ancestor's virtual reuses the ancestor's
  slot: adding or dropping one is no vtable layout change, whether the
  vtable list names every slot (headers) or only the class's own virtuals
  (DWARF). A virtual that overrides nothing still changes it.
"""

from __future__ import annotations

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.compare.covariant_return import narrowed_to_offset_zero_base
from abicheck.model import AbiSnapshot, Function, RecordType


def _rec(name: str, bases=(), offsets=None, virtual=(), vtable=None) -> RecordType:
    kw = {}
    if vtable is not None:
        kw["vtable"] = list(vtable)
    return RecordType(
        name=name,
        kind="struct",
        size_bits=64,
        bases=list(bases),
        virtual_bases=list(virtual),
        base_offsets=dict(offsets or {}),
        **kw,
    )


def _snap(funcs, types) -> AbiSnapshot:
    return AbiSnapshot(
        library="libt.so", version="1", functions=list(funcs), types=list(types)
    )


def _getter(ret: str) -> Function:
    return Function(name="get", mangled="_Z3getv", return_type=ret)


# (old return, new return, records of the new side, covariant?)
# Records: B, A have no bases; the oracle column states the offset rule.
_A = _rec("A")
_B = _rec("B")
_CASES = [
    ("B *", "D *", [_B, _rec("D", ["B"], {"B": 0})], True),
    ("B &", "D &", [_B, _rec("D", ["B"], {"B": 0})], True),
    ("const B *", "const D *", [_B, _rec("D", ["B"], {"B": 0})], True),
    # two levels, each at 0
    ("B *", "E *", [_B, _rec("D", ["B"], {"B": 0}), _rec("E", ["D"], {"D": 0})], True),
    # B is the second base, at 32 bits: the address moves
    ("B *", "D *", [_A, _B, _rec("D", ["A", "B"], {"A": 0, "B": 32})], False),
    # a virtual base is reached through the vbase offset
    ("B *", "D *", [_B, _rec("D", ["B"], {"B": 0}, virtual=["B"])], False),
    # the offset is not known
    ("B *", "D *", [_B, _rec("D", ["B"])], False),
    # cv differs
    ("B *", "const D *", [_B, _rec("D", ["B"], {"B": 0})], False),
    # pointer -> reference
    ("B *", "D &", [_B, _rec("D", ["B"], {"B": 0})], False),
    # widening D* -> B*: B does not derive from D
    ("D *", "B *", [_B, _rec("D", ["B"], {"B": 0})], False),
    # unrelated
    ("B *", "A *", [_A, _B], False),
]


@pytest.mark.parametrize("old_ret,new_ret,records,covariant", _CASES)
def test_return_narrowing_is_compatible_only_at_offset_zero(
    old_ret: str, new_ret: str, records, covariant: bool
) -> None:
    old = _snap([_getter(old_ret)], records)
    new = _snap([_getter(new_ret)], records)
    kinds = {c.kind for c in compare(old, new).changes}
    if covariant:
        assert ChangeKind.FUNC_RETURN_COVARIANT_NARROWED in kinds
        assert ChangeKind.FUNC_RETURN_CHANGED not in kinds
        assert compare(old, new).verdict == Verdict.COMPATIBLE_WITH_RISK
    else:
        assert ChangeKind.FUNC_RETURN_COVARIANT_NARROWED not in kinds
        assert ChangeKind.FUNC_RETURN_CHANGED in kinds


def test_offset_zero_walk_bounds_a_base_cycle() -> None:
    # A malformed snapshot whose base list loops must not recurse forever.
    snap = _snap([], [_rec("X", ["Y"], {"Y": 0}), _rec("Y", ["X"], {"X": 0})])
    assert narrowed_to_offset_zero_base("X", "Z", snap) is False
    assert narrowed_to_offset_zero_base("X", "Y", snap) is True


# --- override slot reuse ---------------------------------------------------------


def _virt(owner: str, leaf: str, mangled: str, idx: int | None = None) -> Function:
    return Function(
        name=f"{owner}::{leaf}",
        mangled=mangled,
        return_type="int",
        is_virtual=True,
        vtable_index=idx,
    )


_BF = _virt("Base", "f", "_ZN4Base1fEv", 2)
_BDTOR = _virt("Base", "~Base", "_ZN4BaseD1Ev", 0)


def _vtable_kinds(old_vt, new_vt, old_fns, new_fns) -> set[ChangeKind]:
    base = _rec("Base", vtable=["_ZN4BaseD1Ev", "_ZN4Base1fEv"])
    old = _snap(old_fns, [base, _rec("Der", ["Base"], {"Base": 0}, vtable=old_vt)])
    new = _snap(new_fns, [base, _rec("Der", ["Base"], {"Base": 0}, vtable=new_vt)])
    return {c.kind for c in compare(old, new).changes}


@pytest.mark.parametrize(
    "old_vt,new_vt,old_extra,new_extra,slot_change",
    [
        # DWARF-style own list: an override of Base::f appears
        (["_ZN3DerD1Ev"], ["_ZN3Der1fEv", "_ZN3DerD1Ev"], [], ["f"], False),
        # ... and disappears
        (["_ZN3Der1fEv", "_ZN3DerD1Ev"], ["_ZN3DerD1Ev"], ["f"], [], False),
        # full list: Base::f's slot now names Der::f
        (
            ["_ZN3DerD1Ev", "_ZN4Base1fEv"],
            ["_ZN3DerD1Ev", "_ZN3Der1fEv"],
            [],
            ["f"],
            False,
        ),
        # full list: the override goes, the slot falls back to Base::f
        (
            ["_ZN3DerD1Ev", "_ZN3Der1fEv"],
            ["_ZN3DerD1Ev", "_ZN4Base1fEv"],
            ["f"],
            [],
            False,
        ),
        # a new virtual g overrides nothing: a slot is added
        (["_ZN3DerD1Ev"], ["_ZN3DerD1Ev", "_ZN3Der1gEv"], [], ["g"], True),
    ],
)
def test_an_override_reuses_its_base_slot(
    old_vt, new_vt, old_extra, new_extra, slot_change: bool
) -> None:
    def fns(extra):
        out = [_BF, _BDTOR, _virt("Der", "~Der", "_ZN3DerD1Ev", 0)]
        if "f" in extra:
            out.append(_virt("Der", "f", "_ZN3Der1fEv", 2))
        if "g" in extra:
            out.append(_virt("Der", "g", "_ZN3Der1gEv", 3))
        return out

    kinds = _vtable_kinds(old_vt, new_vt, fns(old_extra), fns(new_extra))
    assert (ChangeKind.TYPE_VTABLE_CHANGED in kinds) is slot_change


def test_a_same_signature_virtual_in_another_slot_is_not_a_reuse() -> None:
    # Der::f carries slot 5 while Base::f is in slot 2 (a covariant return that
    # needs a this-adjustment gets a slot of its own): the layout changed.
    old_fns = [_BF, _BDTOR, _virt("Der", "~Der", "_ZN3DerD1Ev", 0)]
    new_fns = [*old_fns, _virt("Der", "f", "_ZN3Der1fEv", 5)]
    kinds = _vtable_kinds(
        ["_ZN3DerD1Ev"], ["_ZN3Der1fEv", "_ZN3DerD1Ev"], old_fns, new_fns
    )
    assert ChangeKind.TYPE_VTABLE_CHANGED in kinds
