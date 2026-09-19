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

"""The scoping haystack's deduplication invariants.

Split out of ``test_dumper_scoping.py``, which is at the architecture gate's
1200-line test cap.
"""

from __future__ import annotations

import pytest

from abicheck.model import Function, Param, RecordType, TypeField, Variable

_OWN_HEADER = "/proj/include/own.h"


def _fn(name: str, ret: str = "void", params: tuple[str, ...] = ()) -> Function:
    return Function(
        name=name,
        mangled=f"_Z{len(name)}{name}",
        return_type=ret,
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params)],
        source_header=_OWN_HEADER,
    )


def _rec(
    name: str,
    fields: tuple[tuple[str, str], ...] = (),
    bases: tuple[str, ...] = (),
) -> RecordType:
    return RecordType(
        name=name,
        kind="struct",
        size_bits=64,
        fields=[TypeField(name=n, type=t) for n, t in fields],
        bases=list(bases),
        source_header=_OWN_HEADER,
    )


class TestSignatureHaystackIsDeduplicated:
    """The scanned haystack must not repeat a spelling it already carries.

    **Bug class**: a cost that scales with how often the *same* text is
    repeated rather than with how much distinct text there is. The haystack
    is matched once against an alternation of every candidate spelling, at a
    cost near-linear in its length, so every repeat of
    ``const ns::Thing &`` is rescanned in full. Measured on oneDAL's
    ``libonedal_parameters``: 11,856 texts collapse to 1,822 and 212,006
    characters to 85,654, and deduplicating took the whole dump from 116.9 s
    to 85.4 s with a byte-identical snapshot.

    **General invariant**: for any declaration set, the haystack contains
    each distinct signature text exactly once, and the *set* of texts it
    contains is unchanged. Both halves matter -- dropping duplicates must
    not drop a text that appeared only as a duplicate of nothing, and must
    not invent one.
    """

    @staticmethod
    def _haystack(functions=(), variables=(), types=()):
        from abicheck.dumper_scoping import _kept_signature_haystack

        return _kept_signature_haystack(list(functions), list(variables), list(types))

    def test_a_spelling_shared_by_many_declarations_appears_once(self) -> None:
        shared = "const ns::Thing &"
        fns = [_fn(f"f{i}", ret=shared, params=(shared, shared)) for i in range(50)]
        hay = self._haystack(functions=fns)
        assert hay.split("\n").count(shared) == 1, (
            "a repeated spelling is still rescanned once per occurrence"
        )

    def test_the_set_of_texts_is_preserved_exactly(self) -> None:
        """Vacuity/safety guard: dedup may not add or lose a distinct text."""
        fns = [
            _fn("a", ret="int", params=("A *", "B &")),
            _fn("b", ret="B &", params=("A *",)),
            _fn("c", ret="C", params=()),
        ]
        recs = [_rec("R", fields=(("f", "D *"),), bases=("A *",))]
        variables = [Variable(name="v", type="E", mangled="_Z1v")]
        hay = self._haystack(functions=fns, variables=variables, types=recs)
        got = set(hay.split("\n"))
        assert got == {"int", "A *", "B &", "C", "D *", "E"}, got
        assert "" not in got, "an empty text must still be filtered out"

    @pytest.mark.parametrize("repeats", [1, 2, 17])
    def test_length_tracks_distinct_text_not_repetition(self, repeats) -> None:
        """The property the performance rests on, stated over the axis that
        caused it: growing repetition alone must not grow the haystack."""
        fns = [_fn(f"f{i}", ret="const ns::Thing &") for i in range(repeats)]
        hay = self._haystack(functions=fns)
        assert hay == "const ns::Thing &", (
            f"{repeats} identical signatures produced {len(hay)} characters"
        )

    def test_order_is_deterministic_across_builds(self) -> None:
        """`dict.fromkeys`, not `set`: a reproducible haystack keeps the
        snapshot cache key and any diagnostic stable run to run."""
        fns = [_fn(f"f{i}", ret=f"T{i % 7}") for i in range(40)]
        first = self._haystack(functions=fns)
        assert first == self._haystack(functions=list(fns)), "order varied"
        assert first.split("\n") == [f"T{i}" for i in range(7)], first
