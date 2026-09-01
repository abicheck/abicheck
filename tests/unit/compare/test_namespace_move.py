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
property tests" guidance) as well as through ``compare``. Co-located with the
``compare/namespace_move.py`` production module it exercises (ADR-061 D10:
tests migrate with their production implementation); qualified-name/scope-
component parsing edge cases live in the sibling
``test_scope_component_parsing.py`` instead, since those exercise
``diff_cxx_rules`` directly rather than the namespace-move grouping/emission
entry points this file owns.
"""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.compare.namespace_move import (
    emit_namespace_move_batches,
    find_namespace_move_groups,
)
from abicheck.diff_cxx_rules import (
    component_embeds_template_args,
    itanium_scope_components,
    itanium_scope_components_with_template_positions,
)
from abicheck.diff_symbols_renames import find_prefix_rename_pairs
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
        removed = set(_D1) | {
            "_ZN4old11fEv",
            "_ZN4old11gEv",
            "_ZN4old21fEv",
            "_ZN4old21gEv",
        }
        added = set(_D2) | {
            "_ZN4new11fEv",
            "_ZN4new11gEv",
            "_ZN4new21fEv",
            "_ZN4new21gEv",
        }
        groups = find_namespace_move_groups(removed, added)
        assert ("d1", "d2") in groups
        assert len(groups[("d1", "d2")]) == len(_D1)
        assert ("old1", "new1") not in groups
        assert ("old1", "new2") not in groups
        assert ("old2", "new1") not in groups
        assert ("old2", "new2") not in groups

    def test_independent_moves_reusing_a_bare_segment_name_are_not_rejected(
        self,
    ) -> None:
        """Codex review, fresh evidence: an earlier revision of the
        ambiguity guard computed ambiguity GLOBALLY off the bare segment
        STRING (does "old" ever map to more than one target anywhere?) --
        which wrongly rejected two genuinely independent, individually
        unambiguous moves that merely happen to reuse the same bare
        segment name in different scopes: ``p1::old::{f,g} ->
        p1::new1::{f,g}`` and the unrelated ``p2::old::{h,i} ->
        p2::new2::{h,i}``. Each removed symbol's masked lookup here has
        exactly ONE candidate (the leaf sets {f,g} and {h,i} don't
        overlap, so there's no real confusion about which added symbol is
        the target) -- ambiguity must be judged at that local, per-symbol
        granularity, not by a global scan over bare segment strings."""
        removed = {
            "_ZN2p13old1fEv",
            "_ZN2p13old1gEv",
            "_ZN2p23old1hEv",
            "_ZN2p23old1iEv",
        }
        added = {
            "_ZN2p14new11fEv",
            "_ZN2p14new11gEv",
            "_ZN2p24new21hEv",
            "_ZN2p24new21iEv",
        }
        groups = find_namespace_move_groups(removed, added)
        assert ("old", "new1") in groups
        assert ("old", "new2") in groups
        assert len(groups[("old", "new1")]) == 2
        assert len(groups[("old", "new2")]) == 2


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
        groups = find_namespace_move_groups(set(_D1_HEADER_TIER), set(_D2_HEADER_TIER))
        assert ("d1", "d2") in groups
        assert len(groups[("d1", "d2")]) == 2

    def test_synthetic_ctor_and_dtor_keys_join_the_same_group_as_mangled_symbols(
        self,
    ) -> None:
        """A real move reports *some* symbols mangled and some through a
        header-tier synthetic key at once -- all of them must land in the
        one substitution group, not split across a recognized group and a
        silently-dropped remainder.

        The ctor/dtor here are each reported through BOTH a real mangled
        symbol AND a header-tier synthetic key -- the same two declarations
        as the plain-mangled _D1 set already covers, just spelled twice.
        The group must count each declaration once (Codex review, fresh
        evidence: an earlier revision appended the identical normalized
        pair once per string identity, double-counting a single moved
        declaration toward the batch-emission support threshold), so the
        total is `len(_D1)` -- not `len(_D1) + len(_D1_HEADER_TIER)`."""
        removed = set(_D1) | set(_D1_HEADER_TIER)
        added = set(_D2) | set(_D2_HEADER_TIER)
        groups = find_namespace_move_groups(removed, added)
        assert ("d1", "d2") in groups
        pairs = dict(groups[("d1", "d2")])
        assert (
            pairs["tbb::detail::d1::graph::{ctor}"] == "tbb::detail::d2::graph::{ctor}"
        )
        assert (
            pairs["tbb::detail::d1::graph::{dtor}"] == "tbb::detail::d2::graph::{dtor}"
        )
        assert len(groups[("d1", "d2")]) == len(_D1)

    def test_reported_through_compare_with_header_tier_keys_only(self) -> None:
        # A class's own ctor+dtor pair alone is deliberately *not* enough
        # support (see `TestEmitNamespaceMoveBatchesRequiresTwoDistinctEntities`
        # below) -- it is one declaring entity, indistinguishable from an
        # unrelated deleted-class/added-class coincidence. A second,
        # independent header-tier declaration (a plain qualified free-form
        # member name, the third shape this fallback covers) is what makes
        # this a genuine multi-entity move.
        old_keys = [*_D1_HEADER_TIER, "tbb::detail::d1::graph::reset"]
        new_keys = [*_D2_HEADER_TIER, "tbb::detail::d2::graph::reset"]
        old = _snap("2021", [_fn("graph", m) for m in old_keys])
        new = _snap("2022", [_fn("graph", m) for m in new_keys])
        result = compare(old, new)
        batch = [c for c in result.changes if c.kind is ChangeKind.SYMBOL_RENAMED_BATCH]
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


class TestFindNamespaceMoveGroupsCountsEachDeclarationOnce:
    """Codex review, fresh evidence: when a single declaration is reported
    under two different string identities in `removed` (a real mangled
    ctor symbol AND a header-tier synthetic key for the SAME move -- see
    `TestHeaderTierKeysAlsoJoinTheNamespaceMove`'s co-matching case), each
    identity independently produced the identical normalized
    ``(old_qualified, new_qualified)`` pair, double-counting one
    declaration toward `emit_namespace_move_batches`' 2+-pairs threshold
    and potentially emitting a false BREAKING batch finding for what was
    really just one moved symbol."""

    def test_one_declaration_reported_two_ways_counts_once(self) -> None:
        removed = {
            "_ZN3tbb6detail2d15graphC1Ev",
            "__abicheck_ctor__tbb::detail::d1::graph()",
        }
        added = {
            "_ZN3tbb6detail2d25graphC1Ev",
            "__abicheck_ctor__tbb::detail::d2::graph()",
        }
        groups = find_namespace_move_groups(removed, added)
        assert groups[("d1", "d2")] == [
            ("tbb::detail::d1::graph::{ctor}", "tbb::detail::d2::graph::{ctor}")
        ]
        # Below the 2-pairs threshold -- a single declaration reported
        # twice must not manufacture a batch finding on its own.
        assert emit_namespace_move_batches(groups) == []


class TestFindNamespaceMoveGroupsRejectsManyToOnePairings:
    """Codex review, fresh evidence: the one-to-many ambiguity guard (see
    ``test_ambiguous_many_to_many_pairing_is_rejected`` above) checks
    whether a REMOVED symbol's masked context matches more than one added
    candidate, but says nothing about the RECIPROCAL shape -- more than one
    DISTINCT removed segment value converging on the identical masked
    context. When ``old1::{f,g}`` and ``old2::{f,g}`` are both removed
    while only ``new::{f,g}`` is added, each removed symbol's masked lookup
    has exactly one candidate (``new::f``/``new::g``), so the one-to-many
    check alone accepts BOTH ``old1 -> new`` and ``old2 -> new`` and each
    independently clears the 2+-pairs threshold -- two contradictory
    SYMBOL_RENAMED_BATCH findings for evidence that cannot say which of
    old1/old2 actually moved (the other was simply deleted)."""

    def test_two_old_namespaces_converging_on_one_new_namespace_is_rejected(
        self,
    ) -> None:
        removed = {
            "_ZN4old11fEv",
            "_ZN4old11gEv",
            "_ZN4old21fEv",
            "_ZN4old21gEv",
        }
        added = {"_ZN3new1fEv", "_ZN3new1gEv"}
        assert find_namespace_move_groups(removed, added) == {}

    def test_an_unambiguous_group_survives_alongside_an_unrelated_many_to_one_one(
        self,
    ) -> None:
        """The rejection must be scoped to the colliding masked context,
        not collateral-damage a real, resolvable move sharing no segment
        with it."""
        removed = set(_D1) | {
            "_ZN4old11fEv",
            "_ZN4old11gEv",
            "_ZN4old21fEv",
            "_ZN4old21gEv",
        }
        added = set(_D2) | {"_ZN3new1fEv", "_ZN3new1gEv"}
        groups = find_namespace_move_groups(removed, added)
        assert ("d1", "d2") in groups
        assert len(groups[("d1", "d2")]) == len(_D1)
        assert ("old1", "new") not in groups
        assert ("old2", "new") not in groups

    def test_independent_moves_reusing_a_bare_target_name_are_not_rejected(
        self,
    ) -> None:
        """Two genuinely independent, unambiguous moves that happen to
        reuse the same bare TARGET segment name in different scopes
        (``p1::old1::{f,g} -> p1::new::{f,g}`` alongside the unrelated
        ``p2::old2::{h,i} -> p2::new::{h,i}``) must still both be
        reported: each masked context is scoped by its own unmasked
        siblings (``p1`` vs. ``p2``), so the two moves never collide on
        the same masked key even though both target a segment spelled
        ``new``."""
        removed = {
            "_ZN2p14old11fEv",
            "_ZN2p14old11gEv",
            "_ZN2p24old21hEv",
            "_ZN2p24old21iEv",
        }
        added = {
            "_ZN2p13new1fEv",
            "_ZN2p13new1gEv",
            "_ZN2p23new1hEv",
            "_ZN2p23new1iEv",
        }
        groups = find_namespace_move_groups(removed, added)
        assert groups[("old1", "new")] == [
            ("p1::old1::f", "p1::new::f"),
            ("p1::old1::g", "p1::new::g"),
        ]
        assert groups[("old2", "new")] == [
            ("p2::old2::h", "p2::new::h"),
            ("p2::old2::i", "p2::new::i"),
        ]


class TestFindNamespaceMoveGroupsRejectsCrossPositionManyToOnePairings:
    """Codex review, fresh evidence: the position-scoped many-to-one guard
    above (``TestFindNamespaceMoveGroupsRejectsManyToOnePairings``) only
    catches two old segment values competing for the SAME masked context --
    i.e. differing at the SAME component position. When removed candidates
    differ from the same added symbol at DIFFERENT positions, their masked
    contexts differ too, so that check sees no collision and both
    contradictory pairings survive. Concretely: removing ``p1::old::{f,g}``
    (masking position 0 -> candidate ``new::old::{f,g}``) and
    ``new::p2::{f,g}`` (masking position 1 -> the SAME candidate
    ``new::old::{f,g}``) while adding only ``new::old::{f,g}`` lets both
    ``p1 -> new`` and ``p2 -> old`` independently clear the 2+-pairs
    threshold, over the identical added declarations -- the same added
    symbol cannot simultaneously be evidence that ``p1`` moved to ``new``
    (with ``old`` unchanged) AND that ``p2`` moved to ``old`` (with ``new``
    unchanged)."""

    def test_cross_position_collision_on_one_added_declaration_is_rejected(
        self,
    ) -> None:
        removed = {
            "_ZN2p13old1fEv",
            "_ZN2p13old1gEv",
            "_ZN3new2p21fEv",
            "_ZN3new2p21gEv",
        }
        added = {"_ZN3new3old1fEv", "_ZN3new3old1gEv"}
        assert find_namespace_move_groups(removed, added) == {}

    def test_ns_symbol_sharing_the_collision_at_one_position_is_also_rejected(
        self,
    ) -> None:
        """Codex review, fresh evidence (round 3): an earlier revision of
        this fix treated `ns::old::f`/`ns::old::g` as an unambiguous
        "unrelated" survivor here, on the reasoning that its OWN masking
        position (masking `old`, matching `ns::new::f`) is collision-free
        even though its OTHER position (masking `ns`, matching
        `new::old::f`) collides with `p1`/`p2` there. That reasoning is
        unsound: `ns::old::f` genuinely has TWO live, structurally possible
        fates here -- `ns -> new` (contested with `p1`, unconfirmable) or
        `old -> new` (its own, otherwise-clean candidacy) -- and a
        collision at one position proves the CONTESTED half is
        unconfirmable, not that the OTHER half is thereby confirmed.
        `ns::old::f`'s fate is exactly as undecided as `p1::old::f`'s or
        `new::p2::f`'s, so nothing here should be reported at all -- see
        `test_an_unambiguous_move_with_no_second_candidacy_still_survives`
        below for what a GENUINELY unambiguous unrelated move (only one
        candidacy total, not merely one collision-free position) looks
        like, and that it does still survive alongside this rejection."""
        removed = {
            "_ZN2p13old1fEv",
            "_ZN2p13old1gEv",
            "_ZN3new2p21fEv",
            "_ZN3new2p21gEv",
            "_ZN2ns3old1fEv",
            "_ZN2ns3old1gEv",
        }
        added = {
            "_ZN3new3old1fEv",
            "_ZN3new3old1gEv",
            "_ZN2ns3new1fEv",
            "_ZN2ns3new1gEv",
        }
        assert find_namespace_move_groups(removed, added) == {}

    def test_an_unambiguous_move_with_no_second_candidacy_still_survives(
        self,
    ) -> None:
        """The rejection must be scoped to a removed symbol that genuinely
        has more than one raw candidacy, not collateral-damage a real,
        resolvable move that has only ONE candidacy in total (no second
        masking position produces any candidate at all) -- unlike
        `ns::old::f` above, whose own second position DOES produce a
        (contested) candidate."""
        removed = {
            "_ZN2p13old1fEv",
            "_ZN2p13old1gEv",
            "_ZN3new2p21fEv",
            "_ZN3new2p21gEv",
            "_ZN2ns3old1fEv",
            "_ZN2ns3old1gEv",
            "_ZN2a34old31hEv",
            "_ZN2a34old31iEv",
        }
        added = {
            "_ZN3new3old1fEv",
            "_ZN3new3old1gEv",
            "_ZN2ns3new1fEv",
            "_ZN2ns3new1gEv",
            "_ZN2a34new31hEv",
            "_ZN2a34new31iEv",
        }
        groups = find_namespace_move_groups(removed, added)
        assert groups == {
            ("old3", "new3"): [
                ("a3::old3::h", "a3::new3::h"),
                ("a3::old3::i", "a3::new3::i"),
            ]
        }
        assert ("old", "new") not in groups
        assert ("p1", "new") not in groups
        assert ("p2", "old") not in groups

    def test_cross_position_collision_sharing_identical_key_text_is_rejected(
        self,
    ) -> None:
        """Codex review, fresh evidence: the fix above tracked distinct
        claiming removed-symbol identities per added declaration -- an
        earlier revision tracked distinct ``(old_segment, new_segment)``
        KEY TEXT instead, which is insufficient. Removing ``old::new::f``
        and ``new::old::f`` while adding only ``new::new::f`` has BOTH
        claims spell the identical key ``('old', 'new')`` (``old::new::f``
        masked at position 0 gives ``old -> new``; ``new::old::f`` masked
        at position 1 also gives ``old -> new``), so a key-text-only guard
        wrongly saw one distinct key and accepted both -- even though they
        are two genuinely different removed originals both claiming the
        SAME single added declaration as their target, which cannot
        actually be the result of two different historical moves at once."""
        removed = {"_ZN3old3new1fEv", "_ZN3new3old1fEv"}
        added = {"_ZN3new3new1fEv"}
        assert find_namespace_move_groups(removed, added) == {}


class TestFindNamespaceMoveGroupsRejectsCrossPositionOneToManyPairings:
    """Codex review, fresh evidence: the previous fixes catch several
    removed symbols converging on one added declaration; they say nothing
    about the SYMMETRIC shape -- the SAME removed symbol resolving to
    DIFFERENT added declarations at its different masking positions.
    Removing ``p1::old::{f,g}`` while adding ``new::old::{f,g}`` AND
    ``p1::new::{f,g}`` makes each removed symbol match TWO candidates: at
    masking position 0 (hiding ``p1``) it matches ``new::old::{f,g}``
    (implying ``p1 -> new``); at masking position 1 (hiding ``old``) it
    matches ``p1::new::{f,g}`` (implying ``old -> new``). Both
    substitutions are individually unambiguous by every other check, yet
    the identical removed symbol is being counted as evidence for two
    mutually exclusive moves at once."""

    def test_one_removed_symbol_matching_two_targets_across_positions_is_rejected(
        self,
    ) -> None:
        removed = {"_ZN2p13old1fEv", "_ZN2p13old1gEv"}
        added = {
            "_ZN3new3old1fEv",
            "_ZN3new3old1gEv",
            "_ZN2p13new1fEv",
            "_ZN2p13new1gEv",
        }
        assert find_namespace_move_groups(removed, added) == {}

    def test_unrelated_same_position_collision_also_rejects_the_shared_symbol(
        self,
    ) -> None:
        """This is the identical scenario as
        `TestFindNamespaceMoveGroupsRejectsCrossPositionManyToOnePairings.
        test_ns_symbol_sharing_the_collision_at_one_position_is_also_rejected`
        above, pinned here too since it's the concrete counterexample that
        proved an earlier revision of THIS class's own guard unsound (see
        the round-3 note on `removed_id_to_added_symbols`'s construction):
        `ns::old::f`/`ns::old::g` genuinely have two live candidacies here
        (`ns -> new`, contested with `p1`/`p2`; `old -> new`, its own
        otherwise-clean candidacy) and must be rejected entirely, not
        merely have the contested half discarded in favor of the clean
        half. See `TestFindNamespaceMoveGroupsRejectsCrossPositionManyToOnePairings.
        test_an_unambiguous_move_with_no_second_candidacy_still_survives`
        for what a genuinely single-candidacy unrelated move looks like."""
        removed = {
            "_ZN2p13old1fEv",
            "_ZN2p13old1gEv",
            "_ZN3new2p21fEv",
            "_ZN3new2p21gEv",
            "_ZN2ns3old1fEv",
            "_ZN2ns3old1gEv",
        }
        added = {
            "_ZN3new3old1fEv",
            "_ZN3new3old1gEv",
            "_ZN2ns3new1fEv",
            "_ZN2ns3new1gEv",
        }
        assert find_namespace_move_groups(removed, added) == {}


class TestFindNamespaceMoveGroupsRejectsContestedAlternateCandidacies:
    """Codex review, fresh evidence (round 3) -- the mirror image of
    `TestFindNamespaceMoveGroupsRejectsCrossPositionOneToManyPairings`'s own
    counterexample, found on review of that fix. A removed symbol with two
    raw candidacies must be rejected even when only ONE of its two
    candidacies is itself independently ambiguous (contested by an
    unrelated third symbol at that position) -- the OTHER, locally-clean
    candidacy is not thereby confirmed; it remains merely one of two
    unconfirmed hypotheses. Removing ``ns::old::{f,g}`` and ``ns::q::{f,g}``
    while adding ``new::old::{f,g}`` and ``ns::new::{f,g}``: `ns::old::f`
    matches `new::old::f` cleanly via one masking position (implying
    `ns -> new`) and matches `ns::new::f` via its other masking position
    (implying `old -> new`), but that second candidacy collides with
    `ns::q::f`'s own identical claim on `ns::new::f` (was it `old` or `q`
    that became `new`?). An earlier revision of this fix discarded the
    colliding candidacy and let the clean one survive uncontested, emitting
    a false `ns -> new` batch backed only by `ns::old::f`/`ns::old::g`."""

    def test_alternate_candidacy_contested_by_a_third_symbol_is_still_rejected(
        self,
    ) -> None:
        removed = {
            "_ZN2ns3old1fEv",
            "_ZN2ns3old1gEv",
            "_ZN2ns1q1fEv",
            "_ZN2ns1q1gEv",
        }
        added = {
            "_ZN3new3old1fEv",
            "_ZN3new3old1gEv",
            "_ZN2ns3new1fEv",
            "_ZN2ns3new1gEv",
        }
        assert find_namespace_move_groups(removed, added) == {}


class TestFindNamespaceMoveGroupsRetainsLocallyAmbiguousCandidatesGlobally:
    """Codex review, fresh evidence (round 4) -- a candidacy discarded by
    the LOCAL one-to-many check (a removed symbol's masked context matching
    more than one distinct added target AT THAT POSITION) never entered
    `entries` at all, so it never contributed to the global
    `added_id_to_removed_symbols`/`removed_id_to_added_symbols` collision
    tracking either -- even though discarding it as unusable evidence for
    ONE SPECIFIC pairing does not mean the added declaration it ambiguously
    matched stops being a real, live alternative explanation. Removing
    ``p1::old::{f,g}`` and ``new::p2::{f,g}`` while adding
    ``new::old::{f,g}`` and ``x::old::{f,g}``: `p1::old::f` masked at
    position 0 matches BOTH `new::old::f` and `x::old::f` (locally
    ambiguous, discarded from `entries`), so `new::p2::f` (masking position
    1, matching `new::old::f` uniquely) appeared uncontested and emitted a
    false `p2 -> old` batch, even though `p1::old::f` is just as plausibly
    `new::old::f`'s real source."""

    def test_locally_discarded_candidacy_still_contests_its_target(
        self,
    ) -> None:
        removed = {
            "_ZN2p13old1fEv",
            "_ZN2p13old1gEv",
            "_ZN3new2p21fEv",
            "_ZN3new2p21gEv",
        }
        added = {
            "_ZN3new3old1fEv",
            "_ZN3new3old1gEv",
            "_ZN1x3old1fEv",
            "_ZN1x3old1gEv",
        }
        assert find_namespace_move_groups(removed, added) == {}


class TestEmitNamespaceMoveBatchesRequiresTwoDistinctEntities:
    """Reported false positive (oneCCL, fresh evidence): an unrelated class
    deleted in one scope and an unrelated, differently-named class added in
    the SAME scope always contributes exactly two pairs -- its own
    compiler-generated ``{ctor}``/``{dtor}`` -- to whatever one-component
    substitution their scope chains happen to mask into, regardless of
    whether the class actually moved. ``len(pairs) >= 2`` alone treats that
    as sufficient support; it is not, because a ctor and a dtor of the same
    class are never independent evidence -- they always travel together for
    ANY class, moved or not."""

    def test_one_deleted_class_and_one_unrelated_added_class_is_not_a_batch(
        self,
    ) -> None:
        removed = {
            "__abicheck_ctor__ccl::v1::broadcastExt_attr()",
            "~ccl::v1::broadcastExt_attr",
        }
        added = {
            "__abicheck_ctor__ccl::v1::window()",
            "~ccl::v1::window",
        }
        groups = find_namespace_move_groups(removed, added)
        # The grouping primitive itself still sees two genuine, unambiguous
        # pairs -- the fix belongs at the batch-emission threshold, not here.
        assert groups == {
            ("broadcastExt_attr", "window"): [
                (
                    "ccl::v1::broadcastExt_attr::{ctor}",
                    "ccl::v1::window::{ctor}",
                ),
                (
                    "ccl::v1::broadcastExt_attr::{dtor}",
                    "ccl::v1::window::{dtor}",
                ),
            ]
        }
        assert emit_namespace_move_batches(groups) == []

    def test_a_real_move_of_two_distinct_classes_still_reports(self) -> None:
        """The guard is "one entity's ctor+dtor alone is not enough", not
        "ctor/dtor pairs never count" -- two DIFFERENT classes each moving
        namespace, evidenced only by their ctor/dtor pairs, is genuine
        multi-entity support and must still be reported."""
        removed = {
            "__abicheck_ctor__ccl::v1::Foo()",
            "~ccl::v1::Foo",
            "__abicheck_ctor__ccl::v1::Bar()",
            "~ccl::v1::Bar",
        }
        added = {
            "__abicheck_ctor__ccl::v2::Foo()",
            "~ccl::v2::Foo",
            "__abicheck_ctor__ccl::v2::Bar()",
            "~ccl::v2::Bar",
        }
        groups = find_namespace_move_groups(removed, added)
        changes = emit_namespace_move_batches(groups)
        assert len(changes) == 1
        assert changes[0].kind is ChangeKind.SYMBOL_RENAMED_BATCH
        assert "v1" in changes[0].description and "v2" in changes[0].description

    def test_reported_through_compare(self) -> None:
        """The false positive reproduced end to end: an unrelated class
        deletion + addition in the same scope must never surface as
        SYMBOL_RENAMED_BATCH."""
        old = _snap(
            "13.1",
            [
                _fn(
                    "broadcastExt_attr",
                    "__abicheck_ctor__ccl::v1::broadcastExt_attr()",
                ),
                _fn("~broadcastExt_attr", "~ccl::v1::broadcastExt_attr"),
            ],
        )
        new = _snap(
            "14.0",
            [
                _fn("window", "__abicheck_ctor__ccl::v1::window()"),
                _fn("~window", "~ccl::v1::window"),
            ],
        )
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.SYMBOL_RENAMED_BATCH not in kinds


class TestFindNamespaceMoveGroupsIgnoresTemplateArgumentSubstitutions:
    """Reported mislabel (oneTBB, fresh evidence): a template class whose OWN
    enclosing scope never changed, but whose spelling embeds a template
    argument naming a type that DID move namespace (e.g.
    ``concurrent_priority_queue<tbb::detail::d1::graph_task *>`` ->
    ``concurrent_priority_queue<tbb::detail::d2::graph_task *>``), must not
    be reported as its own "namespace segment" substitution -- the whole
    templated spelling is not a namespace segment, and the real move is
    already reported through ``graph_task``'s own (non-templated) symbols."""

    _OLD_ARG = "tbb::detail::d1::graph_task"
    _NEW_ARG = "tbb::detail::d2::graph_task"
    _TEMPLATE = "concurrent_priority_queue<{}::graph_task *>"

    def test_template_argument_substitution_alone_yields_no_group(self) -> None:
        """With no OTHER evidence of a `d1` -> `d2` move at all, the
        templated ctor/dtor pair must produce nothing -- not even a group
        keyed on the whole instantiation text."""
        removed = {
            f"__abicheck_ctor__tbb::detail::d1::{self._TEMPLATE.format('tbb::detail::d1')}()",
            f"~tbb::detail::d1::{self._TEMPLATE.format('tbb::detail::d1')}",
        }
        added = {
            f"__abicheck_ctor__tbb::detail::d1::{self._TEMPLATE.format('tbb::detail::d2')}()",
            f"~tbb::detail::d1::{self._TEMPLATE.format('tbb::detail::d2')}",
        }
        assert find_namespace_move_groups(removed, added) == {}

    def test_does_not_duplicate_the_real_move_it_is_redundant_with(self) -> None:
        """The real ``d1`` -> ``d2`` move (evidenced by ``graph_task``'s own
        symbols) must still be reported, and reported EXACTLY ONCE -- not
        alongside a second, spurious group keyed on the templated spelling."""
        removed = {
            "tbb::detail::d1::graph_task::run",
            "tbb::detail::d1::graph_task::wait",
            f"__abicheck_ctor__tbb::detail::d1::{self._TEMPLATE.format('tbb::detail::d1')}()",
            f"~tbb::detail::d1::{self._TEMPLATE.format('tbb::detail::d1')}",
        }
        added = {
            "tbb::detail::d2::graph_task::run",
            "tbb::detail::d2::graph_task::wait",
            f"__abicheck_ctor__tbb::detail::d1::{self._TEMPLATE.format('tbb::detail::d2')}()",
            f"~tbb::detail::d1::{self._TEMPLATE.format('tbb::detail::d2')}",
        }
        groups = find_namespace_move_groups(removed, added)
        assert set(groups) == {("d1", "d2")}
        assert len(groups[("d1", "d2")]) == 2
        changes = emit_namespace_move_batches(groups)
        assert len(changes) == 1


class TestComponentEmbedsTemplateArgs:
    """Direct primitive-level tests for the qualified-name/header-tier-
    fallback predicate (CLAUDE.md's "Primitive-level property tests"
    guidance). Text-only, and deliberately NOT used for an Itanium-mangled
    component -- see ``TestItaniumScopeComponentsWithTemplatePositions``
    below for that shape's own, structural predicate."""

    def test_recognizes_pretty_printed_form(self) -> None:
        assert component_embeds_template_args("Box<int>") is True
        assert (
            component_embeds_template_args(
                "concurrent_priority_queue<tbb::detail::d1::graph_task *>"
            )
            is True
        )

    def test_plain_identifiers_are_not_template_bearing(self) -> None:
        assert component_embeds_template_args("graph_task") is False
        assert component_embeds_template_args("run") is False
        assert component_embeds_template_args("Item") is False
        assert component_embeds_template_args("ICE") is False


class TestItaniumScopeComponentsWithTemplatePositions:
    """Direct primitive-level tests for the structural (parse-time, not
    text-guessed) template-position signal a real Itanium mangling uses.
    CodeRabbit/Codex review, fresh evidence: this predicate exists
    specifically because a text-based guess over the assembled raw
    component (the shape ``component_embeds_template_args`` briefly also
    attempted for this case, then reverted) is unsound -- an ordinary
    identifier like ``"ICE"`` parses as a balanced raw ``I...E`` template
    block purely by coincidental spelling, which would silently exclude a
    genuine namespace move of a class named ``ICE`` from ever being
    detected."""

    def test_recognizes_a_real_template_instantiation(self) -> None:
        # `tbb::detail::d1::concurrent_priority_queue<tbb::detail::d1::graph_task *>::graph_task_ptr` (ctor)
        mangled = "_ZN3tbb6detail2d125concurrent_priority_queueIPN3tbb6detail2d110graph_taskEEC1Ev"
        result = itanium_scope_components_with_template_positions(mangled)
        assert result is not None
        comps, template_positions = result
        assert comps == [
            "tbb",
            "detail",
            "d1",
            "concurrent_priority_queueIPN3tbb6detail2d110graph_taskEE",
            "{ctor}",
        ]
        assert template_positions == frozenset({3})

    def test_does_not_misread_a_coincidentally_ice_shaped_identifier(self) -> None:
        """The exact regression this predicate exists to close: a real class
        literally named ``ICE`` moving namespace must not have its own
        component excluded from masking -- unlike the text-based guess this
        function replaces for the Itanium shape."""
        mangled = "_ZN2ns3ICE1fEv"
        result = itanium_scope_components_with_template_positions(mangled)
        assert result is not None
        comps, template_positions = result
        assert comps == ["ns", "ICE", "f"]
        assert template_positions == frozenset()

    def test_plain_symbols_have_no_template_positions(self) -> None:
        result = itanium_scope_components_with_template_positions(
            "_ZN3tbb6detail2d110graph_task3runEv"
        )
        assert result is not None
        comps, template_positions = result
        assert comps == ["tbb", "detail", "d1", "graph_task", "run"]
        assert template_positions == frozenset()

    def test_matches_the_plain_scope_components_list(self) -> None:
        """The list half of a successful result must be identical to what
        ``itanium_scope_components`` (the pre-existing, template-position-
        blind function) returns for the same input."""
        mangled = "_ZN3tbb6detail2d125concurrent_priority_queueIPN3tbb6detail2d110graph_taskEEC1Ev"
        result = itanium_scope_components_with_template_positions(mangled)
        assert result is not None
        assert result[0] == itanium_scope_components(mangled)

    def test_an_unbalanced_directly_attached_template_args_list_returns_none(
        self,
    ) -> None:
        """A class name followed by an opened but never-closed ``I`` template-
        args list (no matching ``E`` anywhere) must degrade to ``None`` --
        the same "unparseable, let the caller fall back" contract every
        other malformed-input branch in this module uses -- rather than
        raising or fabricating a component."""
        assert itanium_scope_components_with_template_positions("_ZN3FooI") is None
        assert itanium_scope_components("_ZN3FooI") is None

    def test_an_empty_nested_name_body_returns_none(self) -> None:
        """``N…E`` immediately closed with no component in between (no
        ``std::`` prefix, nothing parsed before the terminator) must
        degrade to ``None`` rather than an empty, unusable component list."""
        assert itanium_scope_components_with_template_positions("_ZNEi") is None
        assert itanium_scope_components("_ZNEi") is None


class TestFindNamespaceMoveGroupsDoesNotSkipACoincidentallyTemplateShapedName:
    """End-to-end regression for the same fix, through the real detector
    entry points -- not just the primitive."""

    def test_a_real_move_of_a_class_named_ice_is_still_detected(self) -> None:
        removed = {
            "_ZN2ns3ICE1fEv",
            "_ZN2ns3ICE1gEv",
        }
        added = {
            "_ZN2ns3ACE1fEv",
            "_ZN2ns3ACE1gEv",
        }
        groups = find_namespace_move_groups(removed, added)
        assert ("ICE", "ACE") in groups
        assert len(groups[("ICE", "ACE")]) == 2
        changes = emit_namespace_move_batches(groups)
        assert len(changes) == 1


class TestFindNamespaceMoveGroupsIgnoresRawMangledTemplateArguments:
    """Codex review, fresh evidence, on the fix above: a REAL Itanium-mangled
    symbol keeps a directly-attached template-argument list RAW (see
    ``itanium_scope_components``'s own docstring -- ``Box<int>`` mangles to
    the component ``"BoxIiE"``, with no literal ``<`` anywhere), so a naive
    ``"<" in comps[i]`` check never fires for the real, mangled-symbol
    production case this whole fix exists for -- only for the qualified-
    name/header-tier-fallback spelling the sibling test class above uses.
    These tests reproduce the exact reported shape through real mangled
    symbols: ``tbb::detail::d1::concurrent_priority_queue<tbb::detail::d1::
    graph_task *>`` (ctor/dtor mangled with a raw ``I...E`` template-args
    block naming ``tbb::detail::d1::graph_task``) whose OWN enclosing scope
    (``tbb::detail::d1::concurrent_priority_queue``) never moved, alongside
    the real ``d1`` -> ``d2`` move of ``graph_task`` itself."""

    # `tbb::detail::d1::concurrent_priority_queue<tbb::detail::d1::graph_task *>`
    # ctor/dtor, and the `d2`-instantiated sibling -- hand-mangled (not from a
    # real compiler) but structurally well-formed Itanium encodings, verified
    # to parse via `itanium_scope_components` before being used here.
    _OLD_CTOR = "_ZN3tbb6detail2d125concurrent_priority_queueIPN3tbb6detail2d110graph_taskEEC1Ev"
    _NEW_CTOR = "_ZN3tbb6detail2d125concurrent_priority_queueIPN3tbb6detail2d210graph_taskEEC1Ev"
    _OLD_DTOR = "_ZN3tbb6detail2d125concurrent_priority_queueIPN3tbb6detail2d110graph_taskEED1Ev"
    _NEW_DTOR = "_ZN3tbb6detail2d125concurrent_priority_queueIPN3tbb6detail2d210graph_taskEED1Ev"

    def test_component_is_recognized_as_template_bearing(self) -> None:
        """Sanity check on the primitive itself: the raw Itanium component
        differs between old and new (it embeds ``d1``/``d2``), so without the
        fix it would be a candidate differing position."""
        old_comps = itanium_scope_components(self._OLD_CTOR)
        new_comps = itanium_scope_components(self._NEW_CTOR)
        assert old_comps is not None and new_comps is not None
        assert old_comps[3] != new_comps[3]
        assert "<" not in old_comps[3]  # the raw shape, not the pretty one

    def test_template_argument_substitution_alone_yields_no_group(self) -> None:
        removed = {self._OLD_CTOR, self._OLD_DTOR}
        added = {self._NEW_CTOR, self._NEW_DTOR}
        assert find_namespace_move_groups(removed, added) == {}

    def test_does_not_duplicate_the_real_move_it_is_redundant_with(self) -> None:
        removed = {
            "_ZN3tbb6detail2d110graph_task3runEv",
            "_ZN3tbb6detail2d110graph_task4waitEv",
            self._OLD_CTOR,
            self._OLD_DTOR,
        }
        added = {
            "_ZN3tbb6detail2d210graph_task3runEv",
            "_ZN3tbb6detail2d210graph_task4waitEv",
            self._NEW_CTOR,
            self._NEW_DTOR,
        }
        groups = find_namespace_move_groups(removed, added)
        assert set(groups) == {("d1", "d2")}
        assert len(groups[("d1", "d2")]) == 2
        changes = emit_namespace_move_batches(groups)
        assert len(changes) == 1
