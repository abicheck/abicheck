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

"""``SYMBOL_RENAMED_BATCH``: namespace-segment moves, and the destructor noise.

Two independent fixes share this file because they are two halves of the same
detector:

* a whole set of symbols moving namespace (oneTBB 2022's
  ``tbb::detail::d1::X`` -> ``tbb::detail::d2::X``) is now recognized as one
  batch instead of N unpaired removals next to N unpaired additions;
* the pre-existing prefix shape no longer treats ``Wrapper`` -> ``~Wrapper``
  as "a ``~`` prefix was added to a symbol", which it never was.

The grouping primitives are tested directly (per CLAUDE.md's "Primitive-level
property tests" guidance) as well as through ``compare``.
"""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.diff_symbols_renames import (
    emit_namespace_move_batches,
    find_namespace_move_groups,
    find_prefix_rename_pairs,
)
from abicheck.model import AbiSnapshot, Function, Visibility


def _fn(name: str, mangled: str | None = None) -> Function:
    return Function(
        name=name,
        mangled=mangled or name,
        return_type="void",
        visibility=Visibility.PUBLIC,
    )


def _snap(version: str, functions: list[Function]) -> AbiSnapshot:
    return AbiSnapshot(library="libtbb.so", version=version, functions=functions)


# The real shape: oneTBB moved its flow-graph implementation namespace from
# ``tbb::detail::d1`` to ``tbb::detail::d2``, so every flow-graph symbol's
# mangled name changed one length-prefixed scope component (``2d1`` -> ``2d2``).
_D1 = [
    "_ZN3tbb6detail2d15graph5resetEv",
    "_ZN3tbb6detail2d15graph4waitEv",
    "_ZN3tbb6detail2d15graphC1Ev",
    "_ZN3tbb6detail2d15graphD1Ev",
]
_D2 = [m.replace("2d15graph", "2d25graph") for m in _D1]


class TestNamespaceMoveIsRecognizedAsOneBatch:
    def test_groups_the_whole_move_under_one_substitution(self) -> None:
        groups = find_namespace_move_groups(set(_D1), set(_D2))
        assert ("d1", "d2") in groups
        assert len(groups[("d1", "d2")]) == len(_D1)

    def test_emits_a_single_rolled_up_finding(self) -> None:
        changes = emit_namespace_move_batches(
            find_namespace_move_groups(set(_D1), set(_D2))
        )
        assert len(changes) == 1
        assert changes[0].kind is ChangeKind.SYMBOL_RENAMED_BATCH
        assert "d1" in changes[0].description and "d2" in changes[0].description

    def test_reported_through_compare(self) -> None:
        old = _snap("2021", [_fn(m, m) for m in _D1])
        new = _snap("2022", [_fn(m, m) for m in _D2])
        result = compare(old, new)
        batch = [c for c in result.changes if c.kind is ChangeKind.SYMBOL_RENAMED_BATCH]
        assert batch, "namespace move produced no batch roll-up"
        assert batch[0].symbol.startswith("batch_rename:")
        # The roll-up is additive: every moved symbol is still individually
        # gone, and a consumer linked against the old name still fails.
        assert any(c.kind is ChangeKind.FUNC_REMOVED for c in result.changes)

    def test_leaf_only_difference_is_not_a_namespace_move(self) -> None:
        """Two symbols differing only in the *declaration* name are a rename of
        the declaration, not a move of its scope — the prefix shape's job."""
        removed = {"_ZN3lib3foo3runEv", "_ZN3lib3foo4stopEv"}
        added = {"_ZN3lib3foo5run2Ev", "_ZN3lib3foo5stop2Ev"}
        assert find_namespace_move_groups(removed, added) == {}

    def test_one_supporting_pair_is_not_a_batch(self) -> None:
        groups = find_namespace_move_groups({_D1[0]}, {_D2[0]})
        assert emit_namespace_move_batches(groups) == []

    def test_unrelated_removals_and_additions_produce_nothing(self) -> None:
        removed = {"_ZN3lib1a1fEv", "_ZN3lib1b1gEv"}
        added = {"_ZN4othr1c1hEv", "_ZN4othr1d1iEv"}
        assert (
            emit_namespace_move_batches(find_namespace_move_groups(removed, added))
            == []
        )


class TestDestructorKeysAreNotRenameGroupMembers:
    def test_dtor_is_not_a_prefixed_rename_of_its_class_name(self) -> None:
        old = {"a": _fn("Wrapper"), "b": _fn("graph")}
        new = {"x": _fn("~Wrapper"), "y": _fn("~graph")}
        assert find_prefix_rename_pairs(set(old), set(new), old, new) == []

    def test_no_batch_finding_for_dtor_vs_plain_name(self) -> None:
        old = _snap(
            "1", [_fn("Wrapper", "_ZN7WrapperC1Ev"), _fn("graph", "_ZN5graphC1Ev")]
        )
        new = _snap(
            "2", [_fn("~Wrapper", "_ZN7WrapperD1Ev"), _fn("~graph", "_ZN5graphD1Ev")]
        )
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.SYMBOL_RENAMED_BATCH not in kinds

    def test_a_real_dtor_to_dtor_namespace_prefix_still_pairs(self) -> None:
        """The rule is *destructor-ness must agree*, not "destructors are
        excluded" — moving a set of destructors under a namespace is a real
        batch rename."""
        old = {"a": _fn("~Foo"), "b": _fn("~Bar")}
        new = {"x": _fn("ns::~Foo"), "y": _fn("ns::~Bar")}
        assert sorted(find_prefix_rename_pairs(set(old), set(new), old, new)) == [
            ("~Bar", "ns::~Bar"),
            ("~Foo", "ns::~Foo"),
        ]

    def test_ordinary_library_prefix_rename_still_detected(self) -> None:
        old = _snap("1", [_fn("init"), _fn("process"), _fn("cleanup")])
        new = _snap(
            "2", [_fn("mylib_init"), _fn("mylib_process"), _fn("mylib_cleanup")]
        )
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.SYMBOL_RENAMED_BATCH in kinds


# ── Property-level contracts ──────────────────────────────────────────────

_BASES = st.sampled_from(["Wrapper", "graph", "Foo", "task_group"])
_PREFIXES = st.sampled_from(["", "~", "mylib_", "ns::", "ns::~", "X"])


@settings(max_examples=300, deadline=None)
@given(bases=st.lists(_BASES, min_size=1, max_size=4, unique=True), pre=_PREFIXES)
def test_a_destructor_is_never_grouped_with_a_non_destructor(
    bases: list[str], pre: str
) -> None:
    """The invariant issue 3 is about: whatever the spelling, the two halves of
    a rename pair always agree on whether they name a destructor."""
    old_map = {f"o{i}": _fn(b) for i, b in enumerate(bases)}
    new_map = {f"n{i}": _fn(pre + b) for i, b in enumerate(bases)}
    for old_name, new_name in find_prefix_rename_pairs(
        set(old_map), set(new_map), old_map, new_map
    ):
        assert old_name.rsplit("::", 1)[-1].startswith("~") == new_name.rsplit("::", 1)[
            -1
        ].startswith("~")


@settings(max_examples=200, deadline=None)
@given(
    old_seg=st.sampled_from(["d1", "v1", "detail"]),
    new_seg=st.sampled_from(["d1", "v1", "d2", "v2", "impl"]),
    leaves=st.lists(
        st.sampled_from(["run", "stop", "reset", "wait"]),
        min_size=1,
        max_size=4,
        unique=True,
    ),
)
def test_a_namespace_move_group_never_mixes_leaves(
    old_seg: str, new_seg: str, leaves: list[str]
) -> None:
    """Every pair inside one group shares its leaf declaration name and differs
    only in the substituted scope component — a group that mixed leaves would
    mean two unrelated declarations were reported as one move."""

    def mangle(seg: str, leaf: str) -> str:
        return f"_ZN3lib{len(seg)}{seg}{len(leaf)}{leaf}Ev"

    removed = {mangle(old_seg, leaf) for leaf in leaves}
    added = {mangle(new_seg, leaf) for leaf in leaves}
    groups = find_namespace_move_groups(removed, added)
    for (a, b), pairs in groups.items():
        assert a != b
        for old_q, new_q in pairs:
            assert old_q.rsplit("::", 1)[-1] == new_q.rsplit("::", 1)[-1]
            assert old_q.replace(f"::{a}::", f"::{b}::") == new_q


@settings(max_examples=150, deadline=None)
@given(
    leaves=st.lists(
        st.sampled_from(["run", "stop", "reset"]), min_size=1, max_size=3, unique=True
    )
)
def test_an_unchanged_namespace_never_yields_a_group(leaves: list[str]) -> None:
    """No substitution exists when nothing moved, so nothing is ever grouped —
    a self-comparison must stay silent."""
    syms = {f"_ZN3lib2d1{len(leaf)}{leaf}Ev" for leaf in leaves}
    assert find_namespace_move_groups(syms, syms) == {}
