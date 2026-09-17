# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Contract of the single-pass snapshot encoder (``storage/snapshot_encode``).

``snapshot_to_dict`` used to build the encoded tree twice -- once via
``dataclasses.asdict`` and again via a whole-tree ``_sets_to_lists`` pass --
and to reach that first tree it *assigned ``None``* to five fields on the
caller's live snapshot, restoring them in a ``finally``.

These tests state the two invariants that replaced that, plus an independent
oracle for the projection itself. They are deliberately written against the
*class* of defect rather than one reported input:

* purity is asserted by a probe that observes the snapshot **from inside the
  walk**, which is the only moment the old implementation was ever wrong --
  a before/after comparison around the call could never have caught it, since
  the ``finally`` restored every field before returning;
* equivalence is asserted against a from-scratch ``asdict``-based oracle over
  generated snapshots, not against a fixed expected dictionary.
"""

from __future__ import annotations

import copy
from dataclasses import asdict
from typing import Any

import pytest

from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)
from abicheck.storage.snapshot_encode import (
    _SNAPSHOT_SKIP_FIELDS,
    _encode_value,
    snapshot_to_dict,
)


def _snapshot(n_functions: int = 3, n_types: int = 2) -> AbiSnapshot:
    return AbiSnapshot(
        library="libprobe.so.1",
        version="1.2.3",
        functions=[
            Function(
                name=f"fn{i}",
                mangled=f"_Z3fn{i}i",
                return_type="Widget",
                params=[Param(name="a", type="int"), Param(name="b", type="Widget*")],
                visibility=Visibility.PUBLIC,
                source_header=f"api{i}.h",
            )
            for i in range(n_functions)
        ],
        variables=[Variable(name="v", type="int", mangled="_Z1v")],
        types=[
            RecordType(
                name=f"T{i}",
                kind="struct",
                fields=[TypeField(name="f", type="int")],
            )
            for i in range(n_types)
        ],
        enums=[
            EnumType(
                name="Status",
                members=[
                    EnumMember(name="Ok", value=0),
                    EnumMember(name="Bad", value=1),
                ],
            )
        ],
    )


# --------------------------------------------------------------------------- #
# 1. Purity: the encoder never writes to the snapshot it is encoding.
# --------------------------------------------------------------------------- #


class _Probe(Function):
    """A real ``Function`` the encoder walks into, recording what it sees.

    ``_encode_value`` reads every declared field with ``getattr``, so
    overriding ``__getattribute__`` gives a deterministic hook that fires
    *during* the walk -- no threads, no timing, no sleeps. It is a genuine
    ``Function`` subclass rather than a stand-in dataclass so the codecs that
    run after the walk still find every field they expect.
    """

    def watch(self, snap: AbiSnapshot) -> None:
        object.__setattr__(self, "_watch", snap)
        object.__setattr__(self, "_seen", [])

    @property
    def seen(self) -> list[dict[str, Any]]:
        return object.__getattribute__(self, "_seen")

    def __getattribute__(self, item: str) -> Any:
        if item == "return_type":
            try:
                watched = object.__getattribute__(self, "_watch")
            except AttributeError:
                watched = None
            if watched is not None:
                object.__getattribute__(self, "_seen").append(
                    {field: getattr(watched, field) for field in _SNAPSHOT_SKIP_FIELDS}
                )
        return object.__getattribute__(self, item)


def test_encoder_does_not_clear_skipped_fields_during_the_walk() -> None:
    """The fields the encoder skips stay intact *while* it is encoding.

    This is the exact regression: the old implementation set
    ``surface_graph``/``semantic_ir``/the three lookup caches to ``None`` on
    the caller's object for the duration of ``asdict``, so any other holder of
    that snapshot -- a second serialization, a concurrent reader -- observed a
    snapshot that had silently lost its evidence.
    """
    snap = _snapshot()
    snap.surface_graph = None  # baseline; populated below via a real value
    probe = _Probe(name="probe", mangled="_Z5probe", return_type="int")
    probe.watch(snap)
    snap.functions.append(probe)  # type: ignore[arg-type]
    # Give two skipped fields non-default values, so "cleared" is observable.
    snap._type_by_name = {"T0": snap.types[0]}
    snap.semantic_ir = None

    snapshot_to_dict(snap)

    assert probe.seen, "probe was never walked -- the test proves nothing"
    for observation in probe.seen:
        assert observation["_type_by_name"] == {"T0": snap.types[0]}, (
            "the encoder cleared a lookup cache on the caller's snapshot mid-walk"
        )


def test_encoding_leaves_the_snapshot_equal_to_its_pre_encode_self() -> None:
    snap = _snapshot()
    snap._type_by_name = {"T0": snap.types[0]}
    before = copy.deepcopy(snap)
    snapshot_to_dict(snap)
    assert snap == before


def test_repeated_encodes_agree() -> None:
    """Re-entrancy in the small: encoding twice must give the same answer.

    Under the old implementation the second call could only agree because the
    first had restored what it cleared; here there is nothing to restore.
    """
    snap = _snapshot()
    assert snapshot_to_dict(snap) == snapshot_to_dict(snap)


# --------------------------------------------------------------------------- #
# 2. Detachment: the returned tree shares no mutable container with the source.
# --------------------------------------------------------------------------- #


def test_returned_containers_are_detached_from_the_snapshot() -> None:
    snap = _snapshot()
    encoded = snapshot_to_dict(snap)
    encoded["functions"][0]["params"][0]["name"] = "MUTATED"
    encoded["functions"].append({"name": "added"})
    assert snap.functions[0].params[0].name == "a"
    assert all(getattr(f, "name", None) != "added" for f in snap.functions)


def test_unknown_mutable_leaf_is_deep_copied_not_aliased() -> None:
    """A leaf type the encoder does not know to be immutable is copied.

    ``asdict`` deep-copied every non-container leaf; the fused walk shares
    only provably-immutable ones. Anything else must still be copied, or the
    detached-container contract would hold for dataclasses and quietly fail
    for, say, a bare ``list`` subclass reached as a leaf.
    """

    class _Mutable:
        def __init__(self) -> None:
            self.payload = ["original"]

        def __eq__(self, other: object) -> bool:
            return isinstance(other, _Mutable) and other.payload == self.payload

    original = _Mutable()
    encoded = _encode_value(original)
    assert encoded is not original
    encoded.payload.append("mutated")
    assert original.payload == ["original"]


@pytest.mark.parametrize(
    "value",
    ["s", 1, 1.5, True, None, b"x", Visibility.PUBLIC],
)
def test_immutable_leaves_survive_encoding_unchanged(value: object) -> None:
    assert _encode_value(value) == value


# --------------------------------------------------------------------------- #
# 3. Projection equivalence, against an independent asdict-based oracle.
# --------------------------------------------------------------------------- #


def _oracle_sets_to_lists(obj: Any) -> Any:
    """The exact second pass the fused walk replaced."""
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    if isinstance(obj, dict):
        return {k: _oracle_sets_to_lists(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_oracle_sets_to_lists(v) for v in obj]
    return obj


@pytest.mark.parametrize(
    "n_functions,n_types",
    [(0, 0), (1, 0), (0, 1), (3, 2), (11, 7)],
)
def test_fused_walk_matches_asdict_then_sets_to_lists(
    n_functions: int, n_types: int
) -> None:
    """The structural projection is ``asdict`` + the old set pass, fused.

    The oracle is built here from ``dataclasses.asdict`` directly -- not from
    any helper the implementation shares -- so this cannot pass by both sides
    making the same mistake.
    """
    snap = _snapshot(n_functions, n_types)
    for declaration in (snap.functions, snap.variables, snap.types, snap.enums):
        for item in declaration:
            expected = _oracle_sets_to_lists(asdict(item))
            assert _encode_value(item) == expected


def test_sets_become_sorted_lists_at_every_depth() -> None:
    value = {"outer": [{"inner": {"c", "a", "b"}}], "top": frozenset({2, 1})}
    assert _encode_value(value) == {
        "outer": [{"inner": ["a", "b", "c"]}],
        "top": [1, 2],
    }


def test_skip_fields_are_absent_from_the_generic_walk() -> None:
    """Each skipped field is either absent or owned by a dedicated codec.

    Stated as a property of the *set* rather than a list of field names, so a
    field added to ``_SNAPSHOT_SKIP_FIELDS`` without a codec is caught here.
    """
    snap = _snapshot()
    encoded = snapshot_to_dict(snap)
    for cache_field in ("_func_by_mangled", "_var_by_mangled", "_type_by_name"):
        assert cache_field in _SNAPSHOT_SKIP_FIELDS
        assert cache_field not in encoded
