"""Header-order witness on the direct PE/Mach-O header-scoped dump route.

``_try_header_scoped_dump`` bypasses ``dumper.dump()`` for PE and Mach-O, so the
declared-order witness (ADR-082) has to wrap that route itself. Without it a
reordered but equivalent header set is never proven canonical and the profile
gate refuses it on macOS. These tests drive the real route with the native
builders replaced, so the witness, the contract attachment and the fallback
handling are all the production code.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

import abicheck.dumper as dumper_mod
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.workflows.dump.header_scoped import _try_header_scoped_dump

_BUILDER = {"pe": "_dump_pe", "macho": "_dump_macho"}
_THREE = ("a.h", "b.h", "c.h")


def _native(monkeypatch, fmt: str, names_for) -> None:
    """Replace the native PE/Mach-O builder with one whose declarations depend
    only on the header list it is given, as ``names_for(headers)``."""

    def dump(path, headers, *args, **kwargs):
        return AbiSnapshot(
            library="lib",
            version="1.0",
            functions=[
                Function(
                    name=n, mangled=n, return_type="int", visibility=Visibility.PUBLIC
                )
                for n in names_for(list(headers))
            ],
            variables=[],
            from_headers=True,
        )

    monkeypatch.setattr(dumper_mod, _BUILDER[fmt], dump)


def _headers(tmp_path: Path, names=("a.h", "b.h")) -> list[Path]:
    inc = tmp_path / "inc"
    inc.mkdir(exist_ok=True)
    out = []
    for name in names:
        (inc / name).write_text("int f(void);\n")
        out.append(inc / name)
    return out


def _dump(fmt: str, tmp_path: Path, headers: list[Path]):
    return _try_header_scoped_dump(fmt, tmp_path / "x.bin", headers, [], "1.0", "c++")


@pytest.mark.parametrize("fmt", ["pe", "macho"])
def test_reordered_equivalent_headers_are_proven_canonical(
    monkeypatch, tmp_path: Path, fmt: str
) -> None:
    """Positive: a reversed header set whose declarations agree with the sorted
    order records the canonical proof on the snapshot it returns."""
    _native(monkeypatch, fmt, lambda headers: ["f", "g"])
    a, b = _headers(tmp_path)

    snap, reason = _dump(fmt, tmp_path, [b, a])

    assert reason is None, "the native route must succeed, not fall back"
    assert snap is not None
    assert snap.contract is not None
    assert snap.contract.header_canonical == ["a.h", "b.h"]


@pytest.mark.parametrize("fmt", ["pe", "macho"])
def test_order_dependent_declarations_stay_unproven(
    monkeypatch, tmp_path: Path, fmt: str
) -> None:
    """Negative control: when the declarations depend on which header comes
    first, the sorted order does not reproduce them, so no proof is recorded.
    The declared snapshot is still returned unchanged."""
    _native(monkeypatch, fmt, lambda headers: [f"first_{headers[0].stem}"])
    a, b = _headers(tmp_path)

    snap, reason = _dump(fmt, tmp_path, [b, a])

    assert reason is None
    assert snap is not None
    assert snap.contract is not None
    assert snap.contract.header_canonical is None
    assert [f.name for f in snap.declarations.functions] == ["first_b"]


@pytest.mark.parametrize("fmt", ["pe", "macho"])
def test_already_sorted_order_is_its_own_proof(
    monkeypatch, tmp_path: Path, fmt: str
) -> None:
    _native(monkeypatch, fmt, lambda headers: ["f", "g"])
    a, b = _headers(tmp_path)

    snap, reason = _dump(fmt, tmp_path, [a, b])

    assert reason is None
    assert snap is not None
    assert snap.contract is not None
    assert snap.contract.header_canonical == ["a.h", "b.h"]


@pytest.mark.parametrize("fmt", ["pe", "macho"])
@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
def test_every_declared_order_of_an_order_free_set_is_proven(
    monkeypatch, tmp_path: Path, fmt: str, order: tuple[int, ...]
) -> None:
    """Sibling cases beyond the two-header pair: all six declared orders of a
    three-header set whose declarations do not depend on order must each record
    the same canonical sequence, taken from the literal sorted names."""
    _native(monkeypatch, fmt, lambda headers: ["f", "g"])
    headers = _headers(tmp_path, _THREE)

    snap, reason = _dump(fmt, tmp_path, [headers[i] for i in order])

    assert reason is None
    assert snap is not None and snap.contract is not None
    assert snap.contract.header_canonical == list(_THREE)
