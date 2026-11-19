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

"""Affected-symbol attribution matches type names as whole identifiers.

Found by the closed layout fragment (``formal/FRAGMENT.md``) and re-measured
by the record-edit fragment (``formal/RECORD_EDIT_FRAGMENT.md``: 925 of 9252
records): a change to ``struct s1`` listed ``use_s10 …`` as affected because
the enrichment related a type to every signature *containing* its name.
Oracle: a lookaround regular expression, not the implementation's scan.
"""

from __future__ import annotations

import random
import re

import pytest

from abicheck.compare.type_name_boundary import occurs_as_identifier
from abicheck.diff_filtering import _build_type_embed_index, _build_type_to_funcs
from abicheck.model import AbiSnapshot, Function, Param, RecordType, TypeField


def _oracle(name: str, text: str) -> bool:
    return (
        re.search(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])", text)
        is not None
    )


def _fn(n: str, t: str) -> Function:
    return Function(
        name=f"use_{n}",
        mangled=f"use_{n}",
        return_type="int",
        params=[Param(name="p", type=t)],
    )


def test_prefix_of_another_type_is_not_attributed_to_its_functions() -> None:
    funcs = {f"use_s{n}": _fn(f"s{n}", f"struct s{n} *") for n in (1, 10, 2, 100)}
    to_funcs, _ = _build_type_to_funcs({"s1", "s10"}, funcs)
    assert to_funcs == {"s1": {"use_s1"}, "s10": {"use_s10"}}


@pytest.mark.parametrize(
    ("spelling", "hit"),
    [
        ("struct Widget *", True),
        ("const Widget &", True),
        ("Widget[4]", True),
        ("ns::Widget *", True),
        ("std::vector<Widget>", True),
        ("void (*)(Widget *)", True),
        ("Widget2 *", False),
        ("MyWidget *", False),
        ("Widget_t", False),
        ("struct Widgets *", False),
    ],
)
def test_sibling_spellings(spelling: str, hit: bool) -> None:
    to_funcs, _ = _build_type_to_funcs({"Widget"}, {"f": _fn("f", spelling)})
    assert (to_funcs["Widget"] == {"use_f"}) is hit
    assert _oracle("Widget", spelling) is hit


def test_embedding_index_uses_the_same_boundary() -> None:
    snap = AbiSnapshot(library="l", version="1")
    snap.declarations.types = [
        RecordType(
            name="outer", kind="struct", fields=[TypeField(name="a", type="s10")]
        ),
        RecordType(
            name="holder", kind="struct", fields=[TypeField(name="b", type="s1")]
        ),
    ]
    assert _build_type_embed_index({"s1"}, snap) == {"s1": {"holder"}}


def test_matches_the_lookaround_oracle_on_generated_spellings() -> None:
    rng = random.Random(2026)
    alphabet = "ab_1:*& <>"
    for _ in range(4000):
        name = rng.choice(["a", "ab", "a1", "b_", "ab1"])
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 12)))
        assert occurs_as_identifier(name, text) == _oracle(name, text), (name, text)


def test_ancestor_attribution_uses_the_same_boundary() -> None:
    """Only ``Leaf`` changes; the unchanged ``s1`` embeds it. Functions taking
    ``s1`` are affected through the ancestor walk, ``use_s10`` is not."""
    from abicheck.diff_filtering import _enrich_affected_symbols
    from abicheck.model.change import Change
    from abicheck.model.change_catalog.kinds import ChangeKind

    snap = AbiSnapshot(library="l", version="1")
    snap.declarations.types = [
        RecordType(
            name="Leaf", kind="struct", fields=[TypeField(name="x", type="int")]
        ),
        RecordType(name="s1", kind="struct", fields=[TypeField(name="l", type="Leaf")]),
        RecordType(name="s10", kind="struct", fields=[TypeField(name="y", type="int")]),
    ]
    snap.declarations.functions = [_fn("s1", "struct s1 *"), _fn("s10", "struct s10 *")]
    change = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Leaf", description="d")
    _enrich_affected_symbols([change], snap)
    assert "use_s1" in change.affected_symbols
    assert "use_s10" not in change.affected_symbols
