"""``Variable.initializer_hex`` survives the snapshot codec (generation 82).

A round trip must keep the bytes exactly. A snapshot written before generation
82 has no key at all: it must decode to ``None`` (not read, unknown), and a
comparison against it must not report a changed initializer.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from test_var_value_bytes_oracle import build_shared

from abicheck.checker import compare
from abicheck.dumper import dump
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

pytestmark = pytest.mark.integration


def _var(snap, name: str):
    (v,) = [x for x in snap.declarations.variables if x.name == name]
    return v


@pytest.fixture()
def snaps(tmp_path: Path):
    old = dump(build_shared("const int g_k = 3;\n", tmp_path / "old.so"), headers=[])
    new = dump(build_shared("const int g_k = 4;\n", tmp_path / "new.so"), headers=[])
    return old, new


def test_round_trip_keeps_the_bytes(snaps) -> None:
    old, _ = snaps
    expected = _var(old, "g_k").initializer_hex
    assert expected == "03000000"
    decoded = snapshot_from_dict(snapshot_to_dict(old))
    assert _var(decoded, "g_k").initializer_hex == expected


def test_a_snapshot_without_the_key_decodes_to_unknown(snaps) -> None:
    old, new = snaps
    raw = snapshot_to_dict(old)
    for v in raw["variables"]:
        v.pop("initializer_hex", None)
    legacy = snapshot_from_dict(raw)
    assert _var(legacy, "g_k").initializer_hex is None


def test_a_legacy_old_side_is_not_a_value_change(snaps) -> None:
    # Old side from a pre-82 snapshot (no bytes), new side read: unknown on the
    # old side means no finding, never "changed".
    old, new = snaps
    raw = copy.deepcopy(snapshot_to_dict(old))
    for v in raw["variables"]:
        v.pop("initializer_hex", None)
    legacy = snapshot_from_dict(raw)
    changes = [
        c
        for c in compare(legacy, new).changes
        if c.kind is ChangeKind.VAR_VALUE_CHANGED
    ]
    assert changes == []
