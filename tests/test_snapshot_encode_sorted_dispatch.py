# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""The per-class dispatch of ``_encode_value_sorted`` is a pure speedup.

The oracle is the former per-value ``isinstance`` chain, kept here verbatim
(it is small) and run over the same inputs: outputs must agree in value,
container type, key order and detachment, and the whole written document
must be byte-identical.
"""

from __future__ import annotations

import copy
import datetime
import enum
import json
import pathlib
from dataclasses import dataclass, field
from typing import Any, NamedTuple

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
from abicheck.storage import snapshot_encode as se


def _oracle(obj: Any) -> Any:
    """The pre-dispatch ``_encode_value_sorted``, unchanged."""
    cls = type(obj)
    if cls in se._IMMUTABLE_LEAF_TYPES:
        return obj
    names = se._dataclass_field_names(cls)
    if names is not None:
        return {name: _oracle(getattr(obj, name)) for name in sorted(names)}
    if isinstance(obj, enum.Enum):
        return obj
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    if isinstance(obj, dict):
        return {k: _oracle(v) for k, v in obj.items()}
    if isinstance(obj, tuple):
        if hasattr(obj, "_fields"):
            return type(obj)(*[_oracle(v) for v in obj])
        return [_oracle(v) for v in obj]
    if isinstance(obj, list):
        return [_oracle(v) for v in obj]
    return copy.deepcopy(obj)


class _Color(enum.Enum):
    RED = "red"


class _Point(NamedTuple):
    x: int
    y: list[int]


class _Tagged(str):
    pass


class _Dict(dict):  # type: ignore[type-arg]
    pass


@dataclass
class _Empty:
    pass


@dataclass
class _One:
    only: list[int] = field(default_factory=lambda: [3, 1])


@dataclass
class _Wide:
    zeta: Any = None
    alpha: Any = None
    mid: Any = None


def _structure(value: Any) -> Any:
    """Value plus container types and key order, for an exact comparison."""
    if isinstance(value, dict):
        return (type(value).__name__, [(k, _structure(v)) for k, v in value.items()])
    if isinstance(value, (list, tuple)):
        return (type(value).__name__, [_structure(v) for v in value])
    return (type(value).__name__, value)


_CASES: list[Any] = [
    "s",
    3,
    2.5,
    None,
    True,
    b"x",
    pathlib.PurePosixPath("a/b"),
    datetime.date(2026, 1, 2),
    _Color.RED,
    {3, 1, 2},
    frozenset({"b", "a"}),
    (1, 2, 3),
    (1, [2], {"k": (3,)}),
    [1, "a", None],
    [[1], (2,), {4, 3}],
    {"z": 1, "a": [(_One(),)]},
    _Dict(b=1, a=(2,)),
    _Point(1, [2]),
    _Tagged("t"),
    _Empty(),
    _One(),
    _Wide(zeta={1, 2}, alpha=_Point(0, [1]), mid=[_One(), _Empty(), _Color.RED]),
    [_Wide(), _Wide(mid=(1, (2, _One())))],
]


@pytest.mark.parametrize("value", _CASES, ids=lambda v: type(v).__name__)
def test_dispatching_encoder_matches_the_isinstance_chain(value: Any) -> None:
    assert _structure(se._encode_value_sorted(value)) == _structure(_oracle(value))


def _snapshot() -> AbiSnapshot:
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
            for i in range(4)
        ],
        variables=[Variable(name="v", type="int", mangled="_Z1v")],
        types=[
            RecordType(
                name=f"T{i}", kind="struct", fields=[TypeField(name="f", type="int")]
            )
            for i in range(3)
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


def test_written_document_is_byte_identical_to_the_oracle_encoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snap = _snapshot()
    new = json.dumps(se.sectioned_document_for_write(snap), indent=2)
    monkeypatch.setattr(se, "_encode_value_sorted", _oracle)
    old = json.dumps(se.sectioned_document_for_write(snap), indent=2)
    assert new == old
    assert '"fn3"' in new


def test_encoded_containers_are_detached_from_the_model() -> None:
    one = _One()
    encoded = se._encode_value_sorted(one)
    encoded["only"].append(9)
    assert one.only == [3, 1]
