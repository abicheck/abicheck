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

"""The section codecs' ``_freeze``/``_unfreeze`` exact-type fast paths.

Both codecs dispatch on ``type(value)`` before the general ``Mapping``/
``list`` checks, because the scalar leaves dominate a canonical payload by
count and each was paying a ``Mapping`` ABC ``isinstance`` before being
returned unchanged (measured on a real header snapshot's own sections:
``types`` 0.0357s -> 0.0216s, ``graph`` 1.3515s -> 1.0147s).

A fast path is only a fast path if it is *also* a correct path, and the two
ways this one could go wrong are both about what it might now skip:

1. **A subclass taking the wrong branch.** ``dict``/``list`` subclasses and
   custom ``Mapping`` implementations must still reach the general checks and
   be frozen, not fall through the exact-type tests and be returned raw. The
   exact-type set deliberately excludes them, and these tests pin that.
2. **Losing deep detachment.** Freezing exists so nothing reachable from a
   constructed DTO aliases a caller's mutable objects. A fast path that
   returned a container unchanged would break that silently -- the exact
   aliasing defect the codecs' own docstrings record as having taken two
   review rounds to find.

So the invariant is stated as "the fast-path implementation agrees with the
general-dispatch one for every value shape", checked against an independent
reference implementation written here rather than by re-running the code
under test, plus explicit detachment assertions. Both codecs are covered:
``storage.dto`` imports ``types_section_codec``'s helpers, so the two real
implementations are these two.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

import pytest

from abicheck.storage import graph_section_codec, types_section_codec

CODECS = pytest.mark.parametrize(
    "codec",
    [types_section_codec, graph_section_codec],
    ids=["types_section_codec", "graph_section_codec"],
)


def reference_freeze(value: Any) -> Any:
    """The pre-fast-path dispatch, kept here as an independent oracle.

    Deliberately *not* imported from the module under test: an oracle that
    calls the implementation proves only that the implementation equals
    itself, which is the tautology ``AGENTS.md``'s third-party-boundary
    lesson names explicitly.
    """
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: reference_freeze(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(reference_freeze(item) for item in value)
    return value


def reference_unfreeze(value: Any) -> Any:
    if isinstance(value, MappingProxyType):
        return {key: reference_unfreeze(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [reference_unfreeze(item) for item in value]
    return value


class OddMapping(Mapping):
    """A custom ``Mapping`` that is not a ``dict`` -- must still be frozen."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


class ListSubclass(list):
    """A ``list`` subclass -- must still be frozen to a tuple."""


class StrSubclass(str):
    """A ``str`` subclass -- must miss the exact-scalar set and fall through
    the general checks, which return it unchanged, same as before."""


def _value_shapes() -> list[Any]:
    """Every shape the dispatch distinguishes, including the ones the exact
    fast paths must NOT claim."""
    return [
        None,
        True,
        False,
        0,
        -17,
        3.5,
        float("inf"),
        "",
        "plain",
        StrSubclass("subclassed"),
        [],
        {},
        [1, "a", None, True],
        {"k": "v", "n": 1, "b": False, "none": None},
        {"nested": {"deep": [1, {"deeper": [None, "x"]}]}},
        ListSubclass([1, {"k": [2, 3]}]),
        OddMapping({"a": 1, "b": [1, 2]}),
        {"mixed": OddMapping({"x": ListSubclass([1])})},
        [[[[["deep"]]]]],
        {"empty_list": [], "empty_dict": {}},
    ]


@CODECS
class TestFastPathAgreesWithGeneralDispatch:
    def test_freeze_matches_the_reference_for_every_shape(self, codec: Any) -> None:
        mismatches = [
            value
            for value in _value_shapes()
            if repr(codec._freeze(value)) != repr(reference_freeze(value))
        ]
        assert not mismatches, f"freeze diverged for: {mismatches}"

    def test_frozen_result_types_match_the_reference(self, codec: Any) -> None:
        """``repr`` above would not distinguish a ``dict`` left unfrozen from
        a ``MappingProxyType``, so compare the produced container types too."""

        def shape(value: Any) -> Any:
            if isinstance(value, MappingProxyType):
                return ("map", {k: shape(v) for k, v in value.items()})
            if isinstance(value, tuple):
                return ("tuple", [shape(v) for v in value])
            return type(value).__name__

        mismatches = [
            value
            for value in _value_shapes()
            if shape(codec._freeze(value)) != shape(reference_freeze(value))
        ]
        assert not mismatches, f"frozen shape diverged for: {mismatches}"

    def test_unfreeze_matches_the_reference_for_every_shape(self, codec: Any) -> None:
        mismatches = [
            value
            for value in _value_shapes()
            if repr(codec._unfreeze(codec._freeze(value)))
            != repr(reference_unfreeze(reference_freeze(value)))
        ]
        assert not mismatches, f"unfreeze diverged for: {mismatches}"


@CODECS
class TestSubclassesStillReachTheGeneralChecks:
    def test_custom_mapping_is_frozen_not_passed_through(self, codec: Any) -> None:
        frozen = codec._freeze(OddMapping({"a": 1}))
        assert isinstance(frozen, MappingProxyType)
        assert dict(frozen) == {"a": 1}

    def test_list_subclass_is_frozen_to_a_tuple(self, codec: Any) -> None:
        assert codec._freeze(ListSubclass([1, 2])) == (1, 2)

    def test_bool_is_not_collapsed_into_int(self, codec: Any) -> None:
        """``bool`` subclasses ``int``; the exact-type set lists it in its own
        right, so ``True`` must survive as ``True`` and not as ``1``."""
        frozen = codec._freeze({"flag": True, "count": 1})
        assert frozen["flag"] is True
        assert frozen["count"] == 1
        assert type(frozen["count"]) is int


@CODECS
class TestDeepDetachmentIsPreserved:
    """The aliasing defect the codecs' docstrings say took two rounds to
    find -- a fast path is the most likely way to silently re-introduce it."""

    def test_mutating_the_source_tree_cannot_reach_the_frozen_copy(
        self, codec: Any
    ) -> None:
        source = {"outer": [{"inner": ["original"]}]}
        frozen = codec._freeze(source)

        source["outer"][0]["inner"][0] = "mutated"
        source["outer"].append("appended")
        source["added"] = True

        assert frozen["outer"][0]["inner"][0] == "original"
        assert len(frozen["outer"]) == 1
        assert "added" not in frozen

    def test_every_reachable_container_is_immutable(self, codec: Any) -> None:
        frozen = codec._freeze({"a": [{"b": [1, {"c": "d"}]}]})

        with pytest.raises(TypeError):
            frozen["a"] = 1  # type: ignore[index]
        assert isinstance(frozen["a"], tuple)
        assert isinstance(frozen["a"][0], MappingProxyType)
        assert isinstance(frozen["a"][0]["b"], tuple)
        assert isinstance(frozen["a"][0]["b"][1], MappingProxyType)

    def test_unfreeze_detaches_from_the_frozen_storage(self, codec: Any) -> None:
        frozen = codec._freeze({"a": [{"b": ["x"]}]})
        thawed = codec._unfreeze(frozen)

        thawed["a"][0]["b"][0] = "changed"
        thawed["a"].append("more")

        assert frozen["a"][0]["b"][0] == "x"
        assert len(frozen["a"]) == 1
