"""ADR-063 criterion 4: the debug layout lives only in the IR store.

States the contract over every combination of builder input (not given /
explicit ``None`` / a value) on both debug channels and every way a snapshot
is cloned or round-tripped, with an independent oracle (the input itself):
the store holds exactly what was given, ``replace``/copy/pickle/codec keep it,
an explicit ``None`` clears, and no ``AbiSnapshot.dwarf`` attribute exists.
"""

from __future__ import annotations

import copy
import dataclasses
import itertools
import pickle

import pytest

from abicheck.model.declaration_store import DEBUG_LAYOUT_KINDS
from abicheck.model.dwarf_facts import (
    AdvancedDwarfMetadata,
    DwarfMetadata,
    StructLayout,
)
from abicheck.model.snapshot import AbiSnapshot
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

_GIVEN = {
    "dwarf": DwarfMetadata(
        has_dwarf=True,
        structs={"S": StructLayout(name="S", byte_size=8)},
        base_types={"long double": 16},
    ),
    "dwarf_advanced": AdvancedDwarfMetadata(
        has_dwarf=True, calling_conventions={"f": "normal"}
    ),
}
_STATES = ("absent", "none", "value")


def _kwargs(states: tuple[str, str]) -> dict[str, object]:
    out: dict[str, object] = {}
    for name, state in zip(DEBUG_LAYOUT_KINDS, states):
        if state == "none":
            out[name] = None
        elif state == "value":
            out[name] = _GIVEN[name]
    return out


def _expected(name: str, state: str) -> object:
    return _GIVEN[name] if state == "value" else None


_COMBOS = list(itertools.product(_STATES, repeat=2))


@pytest.mark.parametrize("states", _COMBOS)
@pytest.mark.parametrize(
    "clone",
    [
        lambda s: s,
        lambda s: dataclasses.replace(s, version="2"),
        copy.copy,
        copy.deepcopy,
        lambda s: pickle.loads(pickle.dumps(s)),
        lambda s: snapshot_from_dict(snapshot_to_dict(s)),
    ],
    ids=["live", "replace", "copy", "deepcopy", "pickle", "codec"],
)
def test_store_holds_exactly_the_builder_input(states, clone) -> None:
    snap = clone(AbiSnapshot(library="l", version="1", **_kwargs(states)))
    for name, state in zip(DEBUG_LAYOUT_KINDS, states):
        got = getattr(snap.declarations, DEBUG_LAYOUT_KINDS[name])
        assert got == _expected(name, state)


@pytest.mark.parametrize("states", _COMBOS)
@pytest.mark.parametrize("name", list(DEBUG_LAYOUT_KINDS))
def test_explicit_none_in_replace_clears_and_value_overrides(states, name) -> None:
    snap = AbiSnapshot(library="l", version="1", **_kwargs(states))
    attr = DEBUG_LAYOUT_KINDS[name]
    assert getattr(dataclasses.replace(snap, **{name: None}).declarations, attr) is None
    other = dataclasses.replace(snap, **{name: _GIVEN[name]})
    assert getattr(other.declarations, attr) == _GIVEN[name]
    # The source snapshot is untouched either way.
    idx = list(DEBUG_LAYOUT_KINDS).index(name)
    assert getattr(snap.declarations, attr) == _expected(name, states[idx])


@pytest.mark.parametrize("name", list(DEBUG_LAYOUT_KINDS))
def test_no_backend_named_attribute_exists(name) -> None:
    snap = AbiSnapshot(library="l", version="1", **{name: _GIVEN[name]})
    with pytest.raises(AttributeError, match=DEBUG_LAYOUT_KINDS[name]):
        getattr(snap, name)
    with pytest.raises(AttributeError, match=DEBUG_LAYOUT_KINDS[name]):
        setattr(snap, name, None)
    assert name not in vars(snap)


@pytest.mark.parametrize("states", _COMBOS)
def test_wire_document_keeps_the_historical_keys_and_order(states) -> None:
    doc = snapshot_to_dict(AbiSnapshot(library="l", version="1", **_kwargs(states)))
    keys = list(doc)
    assert keys.index("macho") + 1 == keys.index("dwarf")
    assert keys.index("dwarf") + 1 == keys.index("dwarf_advanced")
    assert keys.index("dwarf_advanced") + 1 == keys.index("sycl")
