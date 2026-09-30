"""ADR-063 Phase 10: ``AbiSnapshot``'s declaration fields live only in the
IR-owned store. Every removed name is refused for both reads and writes (a
stale caller must never silently read ``None`` or create a stray attribute),
while ``dataclasses.replace`` and the builder inputs keep working."""

from __future__ import annotations

import copy
import dataclasses
import pickle

import pytest

from abicheck.model.declaration_store import DECLARATION_KINDS
from abicheck.model.declarations import Function
from abicheck.model.snapshot import AbiSnapshot


def _snap() -> AbiSnapshot:
    return AbiSnapshot(
        library="libx.so",
        version="1",
        functions=[Function(name="f", mangled="f", return_type="int")],
        constants={"K": "1"},
    )


@pytest.mark.parametrize("kind", DECLARATION_KINDS)
def test_reading_a_removed_name_raises_and_names_the_replacement(kind: str) -> None:
    with pytest.raises(AttributeError, match=rf"declarations\.{kind}"):
        getattr(_snap(), kind)


@pytest.mark.parametrize("kind", DECLARATION_KINDS)
def test_writing_a_removed_name_raises_and_creates_nothing(kind: str) -> None:
    snap = _snap()
    with pytest.raises(AttributeError, match=rf"declarations\.{kind}"):
        setattr(snap, kind, [])
    assert kind not in vars(snap)


@pytest.mark.parametrize("kind", DECLARATION_KINDS)
def test_every_kind_is_reachable_through_the_store(kind: str) -> None:
    snap = _snap()
    value = getattr(snap.declarations, kind)
    assert isinstance(value, (list, dict))


@pytest.mark.parametrize(
    "clone",
    [
        lambda s: dataclasses.replace(s, version="2"),
        copy.copy,
        copy.deepcopy,
        lambda s: pickle.loads(pickle.dumps(s)),
    ],
    ids=["replace", "copy", "deepcopy", "pickle"],
)
def test_copies_keep_the_declarations_and_stay_guarded(clone) -> None:
    snap = _snap()
    other = clone(snap)
    assert [f.name for f in other.declarations.functions] == ["f"]
    assert other.declarations.constants == {"K": "1"}
    with pytest.raises(AttributeError):
        other.functions  # noqa: B018


def test_replace_gives_the_copy_its_own_store() -> None:
    # Each snapshot owns its store (re-binding a kind stays local); the
    # containers are shared, as ``replace`` shared plain-field lists.
    snap = _snap()
    other = dataclasses.replace(snap, version="2")
    assert other.declarations is not snap.declarations
    other.declarations.functions = []
    assert [f.name for f in snap.declarations.functions] == ["f"]
