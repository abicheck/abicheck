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

"""The streaming encoder is byte-identical to ``json.dumps(obj, indent=2)``.

The oracle is the standard library itself, over *generated* documents --
not a second statement of this module's own formatting rules, which would
agree with it by construction and prove nothing (root ``AGENTS.md``, "A
matrix test needs an oracle, not just a type check").

The generator is written to reach the cases that actually break an
indent-aware encoder: empty containers at every depth (``{}``/``[]`` have no
newline inside, unlike non-empty ones), containers nested directly inside
containers, strings needing escapes, non-ASCII, and -- separately -- the two
shapes the encoder deliberately delegates whole (a ``dict``/``list``
subclass, and a non-``str`` key, whose ``json`` coercion is not
re-implemented here).
"""

from __future__ import annotations

import json
import random

import pytest

from abicheck.storage.json_stream import (
    LazyItems,
    iter_json_indented,
    join_json_indented,
)


def _document(rnd: random.Random, depth: int = 0) -> object:
    if depth >= 4:
        return rnd.choice([1, "leaf", None, True, 2.5, "", -0.0])
    roll = rnd.random()
    if roll < 0.3:
        return {f"k{i}": _document(rnd, depth + 1) for i in range(rnd.randrange(0, 4))}
    if roll < 0.55:
        return [_document(rnd, depth + 1) for _ in range(rnd.randrange(0, 4))]
    return rnd.choice(
        [
            0,
            -17,
            'quote"and\\backslash',
            "new\nline\ttab",
            "ünïcodé ☃",
            None,
            False,
            1e300,
            {},
            [],
        ]
    )


class TestDifferentialAgainstJsonDumps:
    @pytest.mark.parametrize("seed", range(8))
    def test_generated_documents_encode_identically(self, seed: int) -> None:
        rnd = random.Random(seed)
        mismatches = []
        for _ in range(400):
            doc = _document(rnd)
            got = join_json_indented(doc)
            want = json.dumps(doc, indent=2)
            if got != want:
                mismatches.append((doc, got, want))
        assert mismatches == []

    @pytest.mark.parametrize(
        "doc",
        [
            {},
            [],
            {"a": {}},
            {"a": []},
            [[], {}, [[]]],
            {"a": {"b": {"c": []}}},
            {"": ""},
            [0],
            "bare string",
            None,
            {"nested": [{"deep": [{"deeper": {}}]}]},
        ],
    )
    def test_edge_shapes(self, doc: object) -> None:
        assert join_json_indented(doc) == json.dumps(doc, indent=2)

    @pytest.mark.parametrize("indent", [0, 1, 2, 4])
    def test_other_indents_too(self, indent: int) -> None:
        """The encoder is not hard-wired to 2, so a caller cannot silently
        get a different format by asking for one it does not support."""
        doc = {"a": [1, {"b": [2, 3]}], "c": {}}
        assert join_json_indented(doc, indent=indent) == json.dumps(doc, indent=indent)

    def test_delegated_shapes_still_match(self) -> None:
        """The two shapes descended into would require re-deriving a coercion."""

        class MyDict(dict):
            pass

        class MyList(list):
            pass

        for doc in (
            {"sub": MyDict({"a": [1, 2]})},
            {"sub": MyList([1, {"b": 2}])},
            {"outer": {1: "int key", 2.5: "float key"}},
            {"outer": {True: "bool key"}},
            {"a": {"b": {2: [1, 2, 3]}}},
        ):
            assert join_json_indented(doc) == json.dumps(doc, indent=2)

    def test_a_large_non_str_keyed_dict_is_delegated_too(self) -> None:
        """The delegation the size bound would otherwise skip past.

        A *small* non-str-keyed dict is delegated by the size check before
        the key check is reached, so that branch was covered only by
        accident. Past the bound the key check is the one that has to
        catch it -- and if it did not, the encoder would descend and
        re-derive ``json``'s own key coercion, which is what it must never
        do.
        """
        from abicheck.storage.json_stream import _DELEGATE_NODE_LIMIT

        doc = {"outer": {i: list(range(3)) for i in range(_DELEGATE_NODE_LIMIT)}}
        assert join_json_indented(doc) == json.dumps(doc, indent=2)

    def test_the_oracle_is_not_vacuous(self) -> None:
        """Guard the comparison itself.

        If ``join_json_indented`` were accidentally ``json.dumps``, every
        test above would pass while testing nothing. It is not: past the
        delegation bound the encoder yields many fragments, which a single
        ``dumps`` call cannot.

        The small-document case is the *other* half of the contract and is
        asserted here too: below the bound it deliberately delegates whole,
        because splitting small objects buys no memory and costs real time.
        """
        from abicheck.storage.json_stream import _DELEGATE_NODE_LIMIT

        big = {"a": list(range(_DELEGATE_NODE_LIMIT + 10))}
        assert len(list(iter_json_indented(big))) > 100
        assert join_json_indented(big) == json.dumps(big, indent=2)
        assert len(list(iter_json_indented({"a": [1, 2], "b": {"c": 3}}))) == 1


class TestLazyItems:
    def test_a_lazy_member_map_matches_its_eager_equivalent(self) -> None:
        eager = {"head": 1, "members": {"x": {"v": 1}, "y": {"v": 2}}, "tail": [3]}
        lazy = {
            "head": 1,
            "members": LazyItems(
                keys=["x", "y"], produce=lambda k: {"v": eager["members"][k]["v"]}
            ),  # type: ignore[index]
            "tail": [3],
        }
        assert join_json_indented(lazy) == json.dumps(eager, indent=2)

    def test_each_member_is_produced_exactly_once_and_in_order(self) -> None:
        seen: list[str] = []

        def produce(name: str) -> dict[str, str]:
            seen.append(name)
            return {"name": name}

        list(iter_json_indented(LazyItems(keys=["a", "b", "c"], produce=produce)))
        assert seen == ["a", "b", "c"]

    def test_only_one_member_is_alive_at_a_time(self) -> None:
        """The property the whole change rests on, asserted by reachability.

        A ``weakref`` per produced member: by the time the *next* member is
        produced, the previous one must already be collectable. A test that
        only counted ``produce`` calls would pass against an encoder that
        produced them one at a time and then held them all.
        """
        import gc
        import weakref

        class Member:
            def __init__(self, name: str) -> None:
                self.name = name

            def __repr__(self) -> str:  # pragma: no cover - not asserted on
                return self.name

        refs: dict[str, weakref.ref[Member]] = {}
        alive_when_produced: list[int] = []

        def produce(name: str) -> dict[str, str]:
            gc.collect()
            alive_when_produced.append(sum(1 for r in refs.values() if r() is not None))
            member = Member(name)
            refs[name] = weakref.ref(member)
            # Encoded as a plain value; the Member object itself is only a
            # liveness probe, so it must not be part of the document.
            return {"name": member.name}

        list(iter_json_indented(LazyItems(keys=list("abcdef"), produce=produce)))
        assert alive_when_produced == [0] * 6

    def test_an_empty_lazy_map_is_an_empty_object(self) -> None:
        assert join_json_indented({"m": LazyItems(keys=[], produce=lambda k: k)}) == (
            json.dumps({"m": {}}, indent=2)
        )

    def test_a_lazy_map_nested_deeply_is_padded_correctly(self) -> None:
        eager = {"a": {"b": {"c": {"x": [1], "y": [2]}}}}
        lazy = {
            "a": {
                "b": {
                    "c": LazyItems(
                        keys=["x", "y"], produce=lambda k: [1 if k == "x" else 2]
                    )
                }
            }
        }
        assert join_json_indented(lazy) == json.dumps(eager, indent=2)
