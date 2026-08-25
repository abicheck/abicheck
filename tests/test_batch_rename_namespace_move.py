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
from abicheck.diff_cxx_rules import (
    qualified_name_scope_components,
    strip_trailing_top_level_parameter_list,
)
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

    def test_ambiguous_many_to_many_pairing_is_rejected(self) -> None:
        """Codex review, fresh evidence: two old namespaces and two new
        namespaces sharing the identical leaf set ({f, g}) let the grouping
        loop accumulate full support for EVERY Cartesian-product pairing --
        old1->new1, old1->new2, old2->new1, AND old2->new2 -- with nothing
        in the evidence to say which (if any) is the real move. Emitting
        all four as contradictory BREAKING batch findings would be worse
        than emitting none; the correct answer is no group at all."""
        removed = {
            "_ZN4old11fEv",
            "_ZN4old11gEv",
            "_ZN4old21fEv",
            "_ZN4old21gEv",
        }
        added = {
            "_ZN4new11fEv",
            "_ZN4new11gEv",
            "_ZN4new21fEv",
            "_ZN4new21gEv",
        }
        assert find_namespace_move_groups(removed, added) == {}

    def test_an_unambiguous_group_survives_alongside_an_unrelated_ambiguous_one(
        self,
    ) -> None:
        """A genuinely unambiguous move (`_D1` -> `_D2`) in the same
        comparison as an unrelated, ambiguous many-to-many pairing must
        still be reported -- rejecting the ambiguous segments must not
        collateral-damage a real, resolvable move that shares no segment
        with them."""
        removed = set(_D1) | {"_ZN4old11fEv", "_ZN4old11gEv", "_ZN4old21fEv", "_ZN4old21gEv"}
        added = set(_D2) | {"_ZN4new11fEv", "_ZN4new11gEv", "_ZN4new21fEv", "_ZN4new21gEv"}
        groups = find_namespace_move_groups(removed, added)
        assert ("d1", "d2") in groups
        assert len(groups[("d1", "d2")]) == len(_D1)
        assert ("old1", "new1") not in groups
        assert ("old1", "new2") not in groups
        assert ("old2", "new1") not in groups
        assert ("old2", "new2") not in groups


# A header-tier (L2) backend can leave ``Function.mangled`` unmangled --
# castxml synthesizes ``__abicheck_ctor__<scope>(<params>)`` for a
# constructor whose real mangled name it omitted, and ``~<scope>`` for a
# destructor (see ``dumper_castxml.SYNTHETIC_CTOR_KEY_PREFIX``/
# ``is_synthetic_dtor_key``). Before the qualified-name fallback,
# ``_scope_components`` returned ``None`` for both shapes (neither is an
# Itanium/MSVC mangling), so a namespace move reported through them never
# joined the roll-up -- reproducing the reported 22/35 func_removed / 25/31
# type_removed unpaired-finding shape on real oneTBB data.
_D1_HEADER_TIER = [
    "__abicheck_ctor__tbb::detail::d1::graph()",
    "~tbb::detail::d1::graph",
]
_D2_HEADER_TIER = [
    "__abicheck_ctor__tbb::detail::d2::graph()",
    "~tbb::detail::d2::graph",
]


class TestHeaderTierKeysAlsoJoinTheNamespaceMove:
    def test_synthetic_ctor_dtor_keys_alone_form_a_group(self) -> None:
        """Reproduces the reported gap directly: with *no* mangled evidence at
        all (a pure header-tier snapshot), two synthesized ctor/dtor keys for
        the same class moving namespace must still pair up. Confirmed to
        return ``{}`` before the qualified-name fallback (neither
        ``itanium_scope_components`` nor ``msvc_scope_components`` recognizes
        either key shape)."""
        groups = find_namespace_move_groups(
            set(_D1_HEADER_TIER), set(_D2_HEADER_TIER)
        )
        assert ("d1", "d2") in groups
        assert len(groups[("d1", "d2")]) == 2

    def test_synthetic_ctor_and_dtor_keys_join_the_same_group_as_mangled_symbols(
        self,
    ) -> None:
        """A real move reports *some* symbols mangled and some through a
        header-tier synthetic key at once -- all of them must land in the
        one substitution group, not split across a recognized group and a
        silently-dropped remainder."""
        removed = set(_D1) | set(_D1_HEADER_TIER)
        added = set(_D2) | set(_D2_HEADER_TIER)
        groups = find_namespace_move_groups(removed, added)
        assert ("d1", "d2") in groups
        pairs = dict(groups[("d1", "d2")])
        assert (
            pairs["tbb::detail::d1::graph::{ctor}"]
            == "tbb::detail::d2::graph::{ctor}"
        )
        assert (
            pairs["tbb::detail::d1::graph::{dtor}"]
            == "tbb::detail::d2::graph::{dtor}"
        )
        assert len(groups[("d1", "d2")]) == len(_D1) + len(_D1_HEADER_TIER)

    def test_reported_through_compare_with_header_tier_keys_only(self) -> None:
        old = _snap("2021", [_fn("graph", m) for m in _D1_HEADER_TIER])
        new = _snap("2022", [_fn("graph", m) for m in _D2_HEADER_TIER])
        result = compare(old, new)
        batch = [
            c for c in result.changes if c.kind is ChangeKind.SYMBOL_RENAMED_BATCH
        ]
        assert batch, "namespace move via header-tier keys produced no batch roll-up"

    def test_qualified_function_name_without_any_mangling_also_pairs(self) -> None:
        """A plain, already-qualified display name used directly as the
        snapshot key (no mangled name, no synthetic ctor/dtor marker) is the
        third real shape this fallback needs to cover."""
        removed = {"tbb::detail::d1::graph::reset", "tbb::detail::d1::graph::wait"}
        added = {"tbb::detail::d2::graph::reset", "tbb::detail::d2::graph::wait"}
        groups = find_namespace_move_groups(removed, added)
        assert ("d1", "d2") in groups
        assert len(groups[("d1", "d2")]) == 2

    def test_bare_unqualified_key_still_yields_no_scope(self) -> None:
        """A plain-C-linkage fallback key (no ``::`` at all) carries no
        namespace to substitute, matching an unmodelled mangled form."""
        assert find_namespace_move_groups({"foo"}, {"bar"}) == {}


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


_HEADER_TIER_KEY_SHAPES = st.sampled_from(
    [
        "__abicheck_ctor__{scope}::{leaf}()",
        "~{scope}::{leaf}",
        "{scope}::{leaf}",
    ]
)


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
    shape=_HEADER_TIER_KEY_SHAPES,
)
def test_header_tier_keys_obey_the_same_grouping_invariant_as_mangled_ones(
    old_seg: str, new_seg: str, leaves: list[str], shape: str
) -> None:
    """The same "never mixes leaves" invariant
    (``test_a_namespace_move_group_never_mixes_leaves``) must hold whether the
    scope chain came from a real mangling or from the header-tier
    (unmangled) fallback — a namespace move reported through castxml's
    synthetic ctor/dtor keys, or through a plain qualified display name, is
    the same kind of finding as one reported through real symbols and must
    obey the same shape."""

    def key(seg: str, leaf: str) -> str:
        return shape.format(scope=f"lib::{seg}", leaf=leaf)

    removed = {key(old_seg, leaf) for leaf in leaves}
    added = {key(new_seg, leaf) for leaf in leaves}
    groups = find_namespace_move_groups(removed, added)
    for (a, b), pairs in groups.items():
        assert a != b
        for old_q, new_q in pairs:
            assert old_q.replace(f"::{a}::", f"::{b}::") == new_q


class TestQualifiedNameScopeComponentsRespectsTemplateNesting:
    """Codex review, fresh evidence: a naive ``split("::")`` treats a
    separator INSIDE a template argument as an enclosing scope. For
    ``lib::foo<old::A>``, that would fabricate a middle component
    ``"foo<old"`` -- which can then coincidentally collide with an
    unrelated ``"foo<new"`` from a different instantiation, producing a
    false namespace-move grouping between two type arguments that were
    never renamed at all."""

    def test_splits_only_at_top_level_separators(self) -> None:
        assert qualified_name_scope_components("lib::foo<old::A>") == [
            "lib",
            "foo<old::A>",
        ]
        assert qualified_name_scope_components("ns::Class::method") == [
            "ns",
            "Class",
            "method",
        ]
        assert qualified_name_scope_components("freefunc") == ["freefunc"]

    def test_a_templated_removal_and_addition_never_pair_as_a_namespace_move(
        self,
    ) -> None:
        """The exact repro from review: ``lib::foo<old::A>``/
        ``lib::foo<old::B>`` removed and ``lib::foo<new::A>``/
        ``lib::foo<new::B>`` added must NOT group as a ``foo<old`` ->
        ``foo<new`` namespace move -- these are two distinct template
        instantiations, not a namespace rename."""
        removed = {"lib::foo<old::A>", "lib::foo<old::B>"}
        added = {"lib::foo<new::A>", "lib::foo<new::B>"}
        groups = find_namespace_move_groups(removed, added)
        assert groups == {}

    def test_unbalanced_nesting_returns_none(self) -> None:
        assert qualified_name_scope_components("lib::foo<old::A") is None
        assert qualified_name_scope_components("lib::foo>old::A") is None

    def test_empty_and_degenerate_inputs_return_none(self) -> None:
        assert qualified_name_scope_components("") is None
        assert qualified_name_scope_components("::foo") is None
        assert qualified_name_scope_components("foo::::bar") is None


class TestStripTrailingTopLevelParameterList:
    """CodeRabbit review, fresh evidence: a synthesized ctor key's
    parameter-list suffix (``__abicheck_ctor__<scope>(<params>)``) was
    stripped via a naive ``scope.find("(")``, which matches the FIRST
    ``(`` anywhere -- including one belonging to a function-type template
    argument nested inside the scope itself, truncating the scope well
    before the real parameter list and losing everything after it."""

    def test_strips_the_real_top_level_parameter_list(self) -> None:
        assert (
            strip_trailing_top_level_parameter_list("ns::Holder<void(int)>(int)")
            == "ns::Holder<void(int)>"
        )

    def test_no_parameter_list_is_unchanged(self) -> None:
        assert strip_trailing_top_level_parameter_list("ns::graph") == "ns::graph"

    def test_a_synthetic_ctor_key_with_a_function_type_template_argument_still_pairs(
        self,
    ) -> None:
        """The exact repro shape from review: a class template holding a
        function-type argument, moving namespace the same way plain
        ``tbb::detail::d1`` -> ``tbb::detail::d2`` does elsewhere in this
        file. Confirmed to return ``{}`` (or a corrupted group keyed on a
        truncated/mismatched scope) before the fix."""
        removed = {"__abicheck_ctor__ns::d1::Holder<void(int)>(int)"}
        added = {"__abicheck_ctor__ns::d2::Holder<void(int)>(int)"}
        groups = find_namespace_move_groups(removed, added)
        assert ("d1", "d2") in groups
        (old_q, new_q) = groups[("d1", "d2")][0]
        assert old_q == "ns::d1::Holder<void(int)>::{ctor}"
        assert new_q == "ns::d2::Holder<void(int)>::{ctor}"
