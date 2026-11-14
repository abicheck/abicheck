"""A virtual member function's ``final`` virt-specifier: detection and storage.

Oracle ([class.virtual]/4): only a transition of ``final`` on a function both
sides declare virtual, and both sides read, is a finding -- gaining it is an
``API_BREAK`` (an overrider stops compiling), losing it a risk. An unread side
(``None``) or a non-virtual function is never a finding. The stored snapshot
keeps the tri-state exactly.
"""

from __future__ import annotations

from itertools import product
from pathlib import Path

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.model import AbiSnapshot, Function
from abicheck.serialization import load_snapshot
from abicheck.storage.snapshot_codec import save_snapshot


def _fn(final: bool | None, virtual: bool = True) -> Function:
    return Function(
        name="C::f",
        mangled="_ZN1C1fEv",
        return_type="int",
        is_virtual=virtual,
        is_final=final,
    )


def _snap(fn: Function) -> AbiSnapshot:
    return AbiSnapshot(library="libf.so", version="1", functions=[fn])


_STATES = (None, False, True)


@pytest.mark.parametrize(
    "old,new,virtual", list(product(_STATES, _STATES, (True, False)))
)
def test_only_a_read_transition_on_a_virtual_function_is_a_finding(
    old: bool | None, new: bool | None, virtual: bool
) -> None:
    r = compare(_snap(_fn(old, virtual)), _snap(_fn(new, virtual)))
    kinds = {c.kind for c in r.changes} & {
        ChangeKind.FUNC_BECAME_FINAL,
        ChangeKind.FUNC_LOST_FINAL,
    }
    readable = virtual and old is not None and new is not None
    if readable and old is False and new is True:
        assert kinds == {ChangeKind.FUNC_BECAME_FINAL}
        assert r.verdict == Verdict.API_BREAK
    elif readable and old is True and new is False:
        assert kinds == {ChangeKind.FUNC_LOST_FINAL}
        assert r.verdict == Verdict.COMPATIBLE_WITH_RISK
    else:
        assert kinds == set()


@pytest.mark.parametrize("final", _STATES)
def test_the_stored_snapshot_keeps_the_tri_state(
    final: bool | None, tmp_path: Path
) -> None:
    others = [
        Function(
            name="C::g",
            mangled="_ZN1C1gEv",
            return_type="int",
            is_virtual=True,
            is_final=False,
        ),
        Function(name="h", mangled="_Z1hv", return_type="int"),
    ]
    snap = AbiSnapshot(library="libf.so", version="1", functions=[_fn(final), *others])
    save_snapshot(snap, tmp_path / "s.json")
    back = load_snapshot(tmp_path / "s.json")
    by_name = {f.mangled: f.is_final for f in back.declarations.functions}
    assert by_name["_ZN1C1fEv"] is final
    assert by_name["_ZN1C1gEv"] is False
    # A free function carries no specifier.
    assert by_name["_Z1hv"] is None


def test_no_specifier_read_writes_no_fact(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    save_snapshot(_snap(_fn(None)), path)
    assert "abicheck.final_member_functions" not in path.read_text()
    assert load_snapshot(path).declarations.functions[0].is_final is None


def test_a_read_specifier_is_carried_by_the_extension_fact(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    save_snapshot(_snap(_fn(True)), path)
    text = path.read_text()
    assert "abicheck.final_member_functions" in text
    assert "is_final" not in text
