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

"""Behavioural properties of ``compare.finding_dedup``.

Every property is stated over generated lists of ``Change`` findings and
checked against a brute-force oracle written here as literal rule data (which
kinds are one observation, which one survives, which pairs stay apart). The
oracle never calls a ``finding_dedup`` helper, so a mutation of the merge
logic changes the implementation's answer and not the expected one.

Invariants:

* **Oracle agreement**: the surviving findings are exactly the oracle's, in
  input order (first occurrence wins inside a merge group).
* **Idempotence**: deduplicating a deduplicated list changes nothing.
* **Order independence**: permuting the input changes neither how many
  findings survive nor which ``(symbol, verdict)`` pairs they carry.
* **Conservation**: a dropped finding is merged into a surviving finding
  that names the same symbol, with a verdict at least as strict.
* **Must-merge / must-not-merge**: explicit pairs, one assertion each.
* **Source-replay twins**: the header and replay copies of one observation
  collapse to one, and the survivor's verdict is the stricter of the two.
"""

from __future__ import annotations

from collections import Counter, defaultdict

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from abicheck.compare.finding_dedup import (
    canonicalize_enum_symbol,
    deduplicate_ast_dwarf,
    deduplicate_cross_detector,
    enum_canonical_names,
)
from abicheck.dwarf_metadata import DwarfMetadata, EnumInfo
from abicheck.model import AbiSnapshot, EnumMember, EnumType
from abicheck.model.change import Change
from abicheck.model.change_catalog.assembled import (
    API_BREAK_KINDS,
    BREAKING_KINDS,
    RISK_KINDS,
)
from abicheck.model.change_catalog.kinds import ChangeKind as K
from abicheck.model.identity import EntityId, EntityKind, Namespace

_ALIAS_NOT_RETAINED = "old version NOT retained as alias"

# ---------------------------------------------------------------------------
# Independent oracle: rule data written out here, not imported from the module.
# ---------------------------------------------------------------------------

#: Kinds whose members of one (category, symbol) are one observation. Two
#: findings of one category for one symbol merge; the first one wins.
_CATEGORY: dict[K, str] = {
    K.FUNC_REMOVED: "func_removal",
    K.FUNC_REMOVED_ELF_ONLY: "func_removal",
    K.FUNC_ADDED: "func_addition",
    K.FUNC_ADDED_ELF_ONLY: "func_addition",
    K.VAR_REMOVED: "var_removal",
    K.VAR_REMOVED_ELF_ONLY: "var_removal",
    K.VAR_ADDED: "var_addition",
    K.SYMBOL_VERSION_NODE_REMOVED: "version_def_removal",
    K.SYMBOL_VERSION_DEFINED_REMOVED: "version_def_removal",
    K.ENUM_MEMBER_REMOVED: "enum_member_removed",
    K.ENUM_MEMBER_VALUE_CHANGED: "enum_member_value_changed",
}

_ELF_ONLY_PARTNER = {
    K.FUNC_REMOVED_ELF_ONLY: K.FUNC_VISIBILITY_CHANGED,
    K.VAR_REMOVED_ELF_ONLY: K.VAR_VISIBILITY_CHANGED,
}
_DELETION_PRIMARY = {K.FUNC_DELETED, K.FUNC_DELETED_DWARF}
_DELETION_CONSEQUENCE = {K.FUNC_VISIBILITY_CHANGED, K.FUNC_BECAME_INLINE}


def _rank(kind: K) -> int:
    """Verdict strictness: 4 breaking, 3 API break, 2 risk, 1 compatible."""
    if kind in BREAKING_KINDS:
        return 4
    if kind in API_BREAK_KINDS:
        return 3
    if kind in RISK_KINDS:
        return 2
    return 1


def _oracle_survivors(changes: list[Change]) -> list[int]:
    """Indices of the findings that survive, in input order."""
    dropped: set[int] = set()
    # A visibility finding already reports an export loss: its ELF-only twin
    # with the same symbol is the same observation.
    for i, c in enumerate(changes):
        partner = _ELF_ONLY_PARTNER.get(c.kind)
        if partner is not None and any(
            o.kind is partner and o.symbol == c.symbol for o in changes
        ):
            dropped.add(i)
    # A deleted function's visibility and became-inline findings are its
    # consequences, matched on the mangled symbol.
    deleted = {c.symbol for c in changes if c.kind in _DELETION_PRIMARY}
    for i, c in enumerate(changes):
        if c.kind in _DELETION_CONSEQUENCE and c.symbol in deleted:
            dropped.add(i)
    # A constant's variable copy restates it: pair the two by symbol and
    # values, one for one, in input order.
    by_key: dict[tuple[str, object, object], tuple[list[int], list[int]]] = defaultdict(
        lambda: ([], [])
    )
    for i, c in enumerate(changes):
        if c.kind is K.CONSTANT_CHANGED:
            by_key[(c.symbol, c.old_value, c.new_value)][0].append(i)
        elif c.kind is K.VAR_VALUE_CHANGED:
            by_key[(c.symbol, c.old_value, c.new_value)][1].append(i)
    for consts, variables in by_key.values():
        dropped.update(variables[: min(len(consts), len(variables))])
    # The version-node move already describes an alias change whose old
    # default is not retained, for the same (symbol, old, new).
    moved = {
        (c.symbol, c.old_value, c.new_value)
        for c in changes
        if c.kind is K.SYMBOL_MOVED_VERSION_NODE
    }
    for i, c in enumerate(changes):
        if (
            c.kind is K.SYMBOL_VERSION_ALIAS_CHANGED
            and _ALIAS_NOT_RETAINED in (c.description or "")
            and (c.symbol, c.old_value, c.new_value) in moved
        ):
            dropped.add(i)
    # Category merge: per (category, symbol) the winner is the node-level
    # version removal if present, else the first occurrence.
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, c in enumerate(changes):
        category = _CATEGORY.get(c.kind)
        if i not in dropped and category is not None:
            groups[(category, c.symbol)].append(i)
    for members in groups.values():
        winner = min(
            members,
            key=lambda i: (
                0 if changes[i].kind is K.SYMBOL_VERSION_NODE_REMOVED else 1,
                i,
            ),
        )
        dropped.update(i for i in members if i != winner)
    return [i for i in range(len(changes)) if i not in dropped]


# ---------------------------------------------------------------------------
# Generators.
# ---------------------------------------------------------------------------

_CROSS_KINDS = [
    K.FUNC_REMOVED,
    K.FUNC_REMOVED_ELF_ONLY,
    K.FUNC_ADDED,
    K.FUNC_ADDED_ELF_ONLY,
    K.VAR_REMOVED,
    K.VAR_REMOVED_ELF_ONLY,
    K.VAR_ADDED,
    K.FUNC_VISIBILITY_CHANGED,
    K.VAR_VISIBILITY_CHANGED,
    K.FUNC_DELETED,
    K.FUNC_DELETED_DWARF,
    K.FUNC_BECAME_INLINE,
    K.CONSTANT_CHANGED,
    K.VAR_VALUE_CHANGED,
    K.SYMBOL_VERSION_NODE_REMOVED,
    K.SYMBOL_VERSION_DEFINED_REMOVED,
    K.SYMBOL_MOVED_VERSION_NODE,
    K.SYMBOL_VERSION_ALIAS_CHANGED,
    K.ENUM_MEMBER_REMOVED,
    K.ENUM_MEMBER_VALUE_CHANGED,
    # Not a dedup kind at all: must pass through untouched.
    K.TYPE_SIZE_CHANGED,
]
# Few symbols and few values, so groups actually collide.
_SYMBOLS = ["f_a", "f_b", "_Z3fooi", "K_S::K_V", "Color::RED", "g_v"]
_VALUES = [None, "1", "2"]
_DESCRIPTIONS = [
    "first wording",
    "second wording, a little longer",
    _ALIAS_NOT_RETAINED,
]


@st.composite
def _cross_detector_changes(draw: st.DrawFn) -> list[Change]:
    # Each list draws from a few kinds, so one symbol gets several kinds from
    # one dedup category often enough to exercise the merge rules; drawing
    # from all kinds at once almost never collides.
    kinds = draw(
        st.lists(st.sampled_from(_CROSS_KINDS), min_size=1, max_size=8, unique=True)
    )
    count = draw(st.integers(min_value=0, max_value=12))
    return [
        Change(
            draw(st.sampled_from(kinds)),
            draw(st.sampled_from(_SYMBOLS)),
            draw(st.sampled_from(_DESCRIPTIONS)),
            old_value=draw(st.sampled_from(_VALUES)),
            new_value=draw(st.sampled_from(_VALUES)),
        )
        for _ in range(count)
    ]


_AST_DWARF_KINDS = [
    K.FUNC_ADDED,
    K.VAR_ADDED,
    K.ENUM_MEMBER_REMOVED,
    K.ENUM_MEMBER_VALUE_CHANGED,
    K.ENUM_LAST_MEMBER_VALUE_CHANGED,
    K.TYPE_SIZE_CHANGED,
    K.STRUCT_SIZE_CHANGED,
    K.TYPE_FIELD_REMOVED,
    K.STRUCT_FIELD_REMOVED,
    K.TYPE_FIELD_OFFSET_CHANGED,
    K.STRUCT_FIELD_OFFSET_CHANGED,
]


@st.composite
def _ast_dwarf_changes(draw: st.DrawFn) -> list[Change]:
    count = draw(st.integers(min_value=0, max_value=12))
    return [
        Change(
            draw(st.sampled_from(_AST_DWARF_KINDS)),
            draw(st.sampled_from(["Color::RED", "Color::BLUE", "demo::S", "f_a"])),
            draw(st.sampled_from(["wording one", "wording two"])),
            old_value=draw(st.sampled_from(_VALUES)),
            new_value=draw(st.sampled_from(_VALUES)),
        )
        for _ in range(count)
    ]


PROPERTY = settings(
    max_examples=120,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


def _fingerprint(c: Change) -> tuple[str, str, int]:
    """What a permutation must not change: the symbol, the verdict, and the
    dedup category. Which member of a category survives is decided by a
    documented preference or by first occurrence, so the exact kind inside one
    category is not part of this fingerprint."""
    return (c.symbol, _CATEGORY.get(c.kind, c.kind.value), _rank(c.kind))


# ---------------------------------------------------------------------------
# Properties over generated lists.
# ---------------------------------------------------------------------------


@PROPERTY
@given(_cross_detector_changes())
def test_cross_detector_survivors_match_the_oracle(changes: list[Change]) -> None:
    index = {id(c): i for i, c in enumerate(changes)}
    got = [index[id(c)] for c in deduplicate_cross_detector(changes)]
    assert got == _oracle_survivors(changes)


@PROPERTY
@given(_cross_detector_changes())
def test_cross_detector_is_idempotent(changes: list[Change]) -> None:
    once = deduplicate_cross_detector(changes)
    twice = deduplicate_cross_detector(once)
    assert [id(c) for c in twice] == [id(c) for c in once]


@PROPERTY
@given(_cross_detector_changes(), st.data())
def test_cross_detector_is_order_independent(
    changes: list[Change], data: st.DataObject
) -> None:
    shuffled = data.draw(st.permutations(changes))
    straight = deduplicate_cross_detector(changes)
    permuted = deduplicate_cross_detector(shuffled)
    assert len(permuted) == len(straight)
    assert Counter(map(_fingerprint, permuted)) == Counter(map(_fingerprint, straight))


@PROPERTY
@given(_cross_detector_changes())
def test_every_dropped_finding_merges_into_a_stricter_or_equal_survivor(
    changes: list[Change],
) -> None:
    survivors = deduplicate_cross_detector(changes)
    kept = {id(c) for c in survivors}
    for c in changes:
        if id(c) in kept:
            continue
        partners = [s for s in survivors if s.symbol == c.symbol]
        assert partners, f"{c.kind.value} {c.symbol!r} vanished without a partner"
        assert max(_rank(s.kind) for s in partners) >= _rank(c.kind), (
            f"{c.kind.value} {c.symbol!r} dropped in favour of a weaker verdict"
        )


@PROPERTY
@given(_ast_dwarf_changes(), st.data())
def test_ast_dwarf_is_idempotent_and_order_independent(
    changes: list[Change], data: st.DataObject
) -> None:
    once = deduplicate_ast_dwarf(changes)
    assert [id(c) for c in deduplicate_ast_dwarf(once)] == [id(c) for c in once]
    shuffled = data.draw(st.permutations(changes))
    assert Counter(map(_fingerprint, deduplicate_ast_dwarf(shuffled))) == Counter(
        map(_fingerprint, once)
    )


@PROPERTY
@given(_ast_dwarf_changes())
def test_ast_dwarf_keeps_one_enum_finding_per_kind_and_symbol(
    changes: list[Change],
) -> None:
    enum_kinds = {
        K.ENUM_MEMBER_REMOVED,
        K.ENUM_MEMBER_VALUE_CHANGED,
        K.ENUM_LAST_MEMBER_VALUE_CHANGED,
    }
    survivors = deduplicate_ast_dwarf(changes)
    per_key = Counter((c.kind, c.symbol) for c in survivors if c.kind in enum_kinds)
    assert all(n == 1 for n in per_key.values())
    # And every enum (kind, symbol) present in the input is still represented.
    assert set(per_key) == {(c.kind, c.symbol) for c in changes if c.kind in enum_kinds}


@PROPERTY
@given(_ast_dwarf_changes())
def test_ast_dwarf_never_drops_a_kind_it_does_not_pair(
    changes: list[Change],
) -> None:
    # FUNC_ADDED / VAR_ADDED / TYPE_SIZE_CHANGED have no DWARF twin rule, so
    # each distinct exact (kind, description, symbol, values) survives once.
    passthrough = [
        c for c in changes if c.kind in (K.FUNC_ADDED, K.VAR_ADDED, K.TYPE_SIZE_CHANGED)
    ]
    exact = {
        (c.kind, c.description, c.symbol, c.old_value, c.new_value) for c in passthrough
    }
    survivors = deduplicate_ast_dwarf(changes)
    kept = Counter(
        (c.kind, c.description, c.symbol, c.old_value, c.new_value)
        for c in survivors
        if c.kind in (K.FUNC_ADDED, K.VAR_ADDED, K.TYPE_SIZE_CHANGED)
    )
    assert set(kept) == exact
    assert all(n == 1 for n in kept.values())


# ---------------------------------------------------------------------------
# Explicit must-merge and must-not-merge pairs.
# ---------------------------------------------------------------------------


def _survivor_kinds(changes: list[Change]) -> list[K]:
    return [c.kind for c in deduplicate_cross_detector(changes)]


@pytest.mark.parametrize(
    ("const_val", "var_val", "const_name", "var_name", "expected"),
    [
        pytest.param(
            ("1", "2"),
            ("1", "2"),
            "K_S::K_V",
            "K_S::K_V",
            [K.CONSTANT_CHANGED],
            id="same-name-same-values-merge",
        ),
        pytest.param(
            ("1", "2"),
            ("1", "3"),
            "K_S::K_V",
            "K_S::K_V",
            [K.CONSTANT_CHANGED, K.VAR_VALUE_CHANGED],
            id="different-new-value-apart",
        ),
        pytest.param(
            ("1", "2"),
            ("0", "2"),
            "K_S::K_V",
            "K_S::K_V",
            [K.CONSTANT_CHANGED, K.VAR_VALUE_CHANGED],
            id="different-old-value-apart",
        ),
        pytest.param(
            ("1", "2"),
            ("1", "2"),
            "a::K_V",
            "b::K_V",
            [K.CONSTANT_CHANGED, K.VAR_VALUE_CHANGED],
            id="same-leaf-other-namespace-apart",
        ),
    ],
)
def test_constant_and_variable_copy_merge_only_on_name_and_values(
    const_val: tuple[str, str],
    var_val: tuple[str, str],
    const_name: str,
    var_name: str,
    expected: list[K],
) -> None:
    changes = [
        Change(K.CONSTANT_CHANGED, const_name, "constant", *const_val),
        Change(K.VAR_VALUE_CHANGED, var_name, "variable", *var_val),
    ]
    assert _survivor_kinds(changes) == expected


@pytest.mark.parametrize(
    ("n_constants", "n_variables"),
    [
        pytest.param(1, 2, id="one-constant-two-copies"),
        pytest.param(2, 1, id="two-constants-one-copy"),
        pytest.param(2, 2, id="paired-one-for-one"),
    ],
)
def test_constant_pairing_is_one_for_one(n_constants: int, n_variables: int) -> None:
    changes = [
        Change(K.CONSTANT_CHANGED, "K_S::K_V", "constant", "1", "2")
        for _ in range(n_constants)
    ] + [
        Change(K.VAR_VALUE_CHANGED, "K_S::K_V", "variable", "1", "2")
        for _ in range(n_variables)
    ]
    survivors = _survivor_kinds(changes)
    # Each constant consumes one variable copy; the rest stay visible.
    assert survivors.count(K.CONSTANT_CHANGED) == n_constants
    assert survivors.count(K.VAR_VALUE_CHANGED) == n_variables - min(
        n_constants, n_variables
    )


@pytest.mark.parametrize(
    ("first", "second"),
    [
        pytest.param("f_a", "f_b", id="different-names"),
        pytest.param("_Z3fooi", "_Z3fooc", id="overloads-of-one-name"),
    ],
)
def test_distinct_functions_removed_stay_apart(first: str, second: str) -> None:
    changes = [
        Change(K.FUNC_REMOVED, first, "removed"),
        Change(K.FUNC_REMOVED, second, "removed"),
    ]
    assert [c.symbol for c in deduplicate_cross_detector(changes)] == [first, second]


def test_same_symbol_removed_twice_merges_whatever_the_wording() -> None:
    changes = [
        Change(K.FUNC_REMOVED, "f_a", "removed"),
        Change(K.FUNC_REMOVED_ELF_ONLY, "f_a", "removed, export gone"),
    ]
    survivors = deduplicate_cross_detector(changes)
    assert [(c.kind, c.symbol) for c in survivors] == [(K.FUNC_REMOVED, "f_a")]


def test_removal_and_addition_of_one_symbol_are_distinct_facets() -> None:
    changes = [
        Change(K.VAR_REMOVED, "g_v", "removed"),
        Change(K.VAR_ADDED, "g_v", "added"),
    ]
    assert _survivor_kinds(changes) == [K.VAR_REMOVED, K.VAR_ADDED]


@pytest.mark.parametrize(
    ("elf_only", "visibility", "expected"),
    [
        pytest.param(
            K.FUNC_REMOVED_ELF_ONLY,
            K.FUNC_VISIBILITY_CHANGED,
            [K.FUNC_VISIBILITY_CHANGED],
            id="function-export-loss-keeps-header-aware",
        ),
        pytest.param(
            K.VAR_REMOVED_ELF_ONLY,
            K.VAR_VISIBILITY_CHANGED,
            [K.VAR_VISIBILITY_CHANGED],
            id="variable-export-loss-keeps-header-aware",
        ),
        pytest.param(
            K.VAR_REMOVED_ELF_ONLY,
            K.FUNC_VISIBILITY_CHANGED,
            [K.VAR_REMOVED_ELF_ONLY, K.FUNC_VISIBILITY_CHANGED],
            id="variable-elf-only-not-covered-by-function-visibility",
        ),
    ],
)
def test_elf_only_removal_is_covered_only_by_its_own_visibility_kind(
    elf_only: K, visibility: K, expected: list[K]
) -> None:
    changes = [
        Change(elf_only, "x_sym", "elf only"),
        Change(visibility, "x_sym", "visibility"),
    ]
    assert _survivor_kinds(changes) == expected


def test_became_inline_is_a_consequence_of_deletion_only_for_the_same_symbol() -> None:
    merged = [
        Change(K.FUNC_DELETED, "_Z3fooi", "deleted"),
        Change(K.FUNC_BECAME_INLINE, "_Z3fooi", "became inline"),
    ]
    assert _survivor_kinds(merged) == [K.FUNC_DELETED]
    apart = [
        Change(K.FUNC_DELETED, "_Z3fooi", "deleted"),
        Change(K.FUNC_BECAME_INLINE, "_Z3fooc", "became inline"),
    ]
    assert _survivor_kinds(apart) == [K.FUNC_DELETED, K.FUNC_BECAME_INLINE]


@pytest.mark.parametrize(
    "first_in_list", [True, False], ids=["node-first", "defined-first"]
)
def test_node_level_version_removal_wins_whatever_the_detector_order(
    first_in_list: bool,
) -> None:
    node = Change(K.SYMBOL_VERSION_NODE_REMOVED, "LIB_1", "node removed")
    defined = Change(K.SYMBOL_VERSION_DEFINED_REMOVED, "LIB_1", "definition removed")
    changes = [node, defined] if first_in_list else [defined, node]
    assert _survivor_kinds(changes) == [K.SYMBOL_VERSION_NODE_REMOVED]


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        pytest.param(
            _ALIAS_NOT_RETAINED, [K.SYMBOL_MOVED_VERSION_NODE], id="not-retained-merges"
        ),
        pytest.param(
            "old version retained as alias",
            [K.SYMBOL_MOVED_VERSION_NODE, K.SYMBOL_VERSION_ALIAS_CHANGED],
            id="retained-alias-carries-its-own-fact",
        ),
    ],
)
def test_alias_change_merges_into_node_move_only_when_old_default_is_gone(
    description: str, expected: list[K]
) -> None:
    changes = [
        Change(K.SYMBOL_MOVED_VERSION_NODE, "f_a", "moved", "LIB_1", "LIB_2"),
        Change(K.SYMBOL_VERSION_ALIAS_CHANGED, "f_a", description, "LIB_1", "LIB_2"),
    ]
    assert _survivor_kinds(changes) == expected


# ---------------------------------------------------------------------------
# Source-replay twins: header and replay copies of one observation.
# Each row: (replay kind, header kind, replay symbol, header symbol,
# the surviving kind). The replay copy carries source_only evidence.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("replay_kind", "header_kind", "replay_sym", "header_sym", "survivor"),
    [
        pytest.param(
            K.CONSTEXPR_VALUE_CHANGED,
            K.CONSTANT_CHANGED,
            "K_S::K_V",
            "K_S::K_V",
            K.CONSTANT_CHANGED,
            id="constexpr-value-header-owns-equal-severity",
        ),
        pytest.param(
            K.INLINE_FUNCTION_REMOVED,
            K.INLINE_FUNCTION_REMOVED,
            "demo::f",
            "demo::f",
            K.INLINE_FUNCTION_REMOVED,
            id="inline-removal-header-owns-equal-severity",
        ),
        pytest.param(
            K.UNINSTANTIATED_TEMPLATE_REMOVED,
            K.INLINE_FUNCTION_REMOVED,
            "demo::tpl",
            "demo::tpl",
            K.UNINSTANTIATED_TEMPLATE_REMOVED,
            id="uninstantiated-template-replay-owns-the-name",
        ),
        pytest.param(
            K.DEFAULT_ARGUMENT_CHANGED,
            K.PARAM_DEFAULT_VALUE_CHANGED,
            "foo",
            "foo",
            K.DEFAULT_ARGUMENT_CHANGED,
            id="default-argument-replay-is-stricter-survives",
        ),
        pytest.param(
            K.PUBLIC_TYPEDEF_REMOVED,
            K.TYPEDEF_REMOVED,
            "demo::old_t",
            "demo::old_t",
            K.TYPEDEF_REMOVED,
            id="typedef-removed-header-owns-equal-severity",
        ),
        pytest.param(
            K.SOURCE_DECL_BINARY_SYMBOL_MISMATCH,
            K.FUNC_REMOVED,
            "_Z3fooi",
            "_Z3fooi",
            K.FUNC_REMOVED,
            id="binary-loss-restated-by-replay-header-stricter",
        ),
    ],
)
def test_replay_twin_collapses_to_one_survivor_with_the_stricter_verdict(
    replay_kind: K,
    header_kind: K,
    replay_sym: str,
    header_sym: str,
    survivor: K,
) -> None:
    replay = Change(replay_kind, replay_sym, "source replay")
    replay.evidence_category = "source_only"
    header = Change(header_kind, header_sym, "header tier")
    survivors = deduplicate_cross_detector([replay, header])
    assert len(survivors) == 1
    assert survivors[0].kind is survivor
    assert _rank(survivors[0].kind) == max(_rank(replay_kind), _rank(header_kind))


def test_replay_copy_without_a_header_twin_stays_visible() -> None:
    replay = Change(K.CONSTEXPR_VALUE_CHANGED, "K_S::K_V", "source replay")
    replay.evidence_category = "source_only"
    other = Change(K.CONSTANT_CHANGED, "K_S::K_OTHER", "header tier")
    survivors = deduplicate_cross_detector([replay, other])
    assert [c.kind for c in survivors] == [
        K.CONSTEXPR_VALUE_CHANGED,
        K.CONSTANT_CHANGED,
    ]


def test_distinct_facets_of_one_template_edit_both_survive() -> None:
    body = Change(K.TEMPLATE_BODY_CHANGED, "demo::tpl", "body fingerprint")
    body.evidence_category = "source_only"
    default = Change(K.TEMPLATE_PARAM_DEFAULT_CHANGED, "demo::tpl", "default")
    survivors = deduplicate_cross_detector([body, default])
    assert [c.kind for c in survivors] == [
        K.TEMPLATE_BODY_CHANGED,
        K.TEMPLATE_PARAM_DEFAULT_CHANGED,
    ]


# ---------------------------------------------------------------------------
# Enum spelling bridge: a bare enum name and its qualified spelling are one
# enum only when the snapshot says so unambiguously.
# ---------------------------------------------------------------------------


def _snap_with_enums(*enums: tuple[str | None, ...]) -> AbiSnapshot:
    """A snapshot holding one enum per ``(bare name, qualified name)`` pair.

    A ``None`` qualified name is an enum with no recorded qualification."""
    return AbiSnapshot(
        library="lib.so",
        version="1",
        enums=[
            EnumType(
                name=bare,
                qualified_name=qualified,
                members=[EnumMember(name="RED", value=0)],
                underlying_type="int",
            )
            for bare, qualified in enums
        ],
    )


@pytest.mark.parametrize(
    ("enums", "expected"),
    [
        pytest.param(
            [("Color", "ns::Color")],
            {"Color": "ns::Color", "ns::Color": "ns::Color"},
            id="bare-and-qualified-both-bridge",
        ),
        pytest.param(
            [("Color", "a::Color"), ("Color", "b::Color")],
            {"a::Color": "a::Color", "b::Color": "b::Color"},
            id="namesakes-never-bridge-the-bare-name",
        ),
        pytest.param(
            [("Color", None), ("Color", "ns::Color")],
            {"ns::Color": "ns::Color"},
            id="global-namesake-competes-with-namespaced",
        ),
        pytest.param(
            [("Color", None)],
            {},
            id="unqualified-global-enum-registers-nothing",
        ),
    ],
)
def test_enum_name_bridge_registers_only_unambiguous_spellings(
    enums: list[tuple[str | None, ...]], expected: dict[str, str]
) -> None:
    assert enum_canonical_names(_snap_with_enums(*enums)) == expected


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        pytest.param("Color", "ns::Color", id="enum-level-bare"),
        pytest.param("ns::Color", "ns::Color", id="enum-level-qualified"),
        pytest.param("Color::RED", "ns::Color::RED", id="member-bare"),
        pytest.param("ns::Color::RED", "ns::Color::RED", id="member-qualified"),
        pytest.param("Color::A::B", None, id="member-of-unknown-prefix"),
        pytest.param("Other::RED", None, id="other-enum-member"),
        pytest.param("RED", None, id="bare-member-without-enum"),
    ],
)
def test_enum_symbol_is_canonicalised_by_its_last_separator(
    symbol: str, expected: str | None
) -> None:
    names = {"Color": "ns::Color", "ns::Color": "ns::Color"}
    assert canonicalize_enum_symbol(symbol, names) == expected


def test_bridged_bare_and_qualified_enum_findings_merge_across_tiers() -> None:
    snap = _snap_with_enums(("Color", "ns::Color"))
    header = Change(K.ENUM_MEMBER_REMOVED, "Color::RED", "header tier")
    dwarf = Change(K.ENUM_MEMBER_REMOVED, "ns::Color::RED", "dwarf tier")
    survivors = deduplicate_cross_detector([header, dwarf], snap, snap)
    assert len(survivors) == 1
    assert survivors[0].qualified_name == "ns::Color::RED"


def test_unbridged_namesake_enum_findings_stay_apart() -> None:
    snap = _snap_with_enums(("Color", "a::Color"), ("Color", "b::Color"))
    first = Change(K.ENUM_MEMBER_REMOVED, "a::Color::RED", "first")
    second = Change(K.ENUM_MEMBER_REMOVED, "b::Color::RED", "second")
    survivors = deduplicate_cross_detector([first, second], snap, snap)
    assert [c.symbol for c in survivors] == ["a::Color::RED", "b::Color::RED"]


# ---------------------------------------------------------------------------
# Cross-tier DWARF-vs-AST pairing in deduplicate_ast_dwarf.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dwarf", "ast", "expected"),
    [
        pytest.param(
            Change(K.STRUCT_SIZE_CHANGED, "demo::S", "dwarf", "8", "16"),
            Change(K.TYPE_SIZE_CHANGED, "demo::S", "header", "64", "128"),
            [K.TYPE_SIZE_CHANGED],
            id="size-same-transition-is-one-finding",
        ),
        pytest.param(
            Change(K.STRUCT_SIZE_CHANGED, "demo::S", "dwarf", "8", "24"),
            Change(K.TYPE_SIZE_CHANGED, "demo::S", "header", "64", "128"),
            [K.STRUCT_SIZE_CHANGED, K.TYPE_SIZE_CHANGED],
            id="size-disagreeing-transition-stays-apart",
        ),
        pytest.param(
            Change(K.STRUCT_SIZE_CHANGED, "demo::T", "dwarf", "8", "16"),
            Change(K.TYPE_SIZE_CHANGED, "demo::S", "header", "64", "128"),
            [K.STRUCT_SIZE_CHANGED, K.TYPE_SIZE_CHANGED],
            id="size-other-type-stays-apart",
        ),
        pytest.param(
            Change(K.STRUCT_ALIGNMENT_CHANGED, "demo::S", "dwarf", "4", "8"),
            Change(K.TYPE_ALIGNMENT_CHANGED, "demo::S", "header", "32", "64"),
            [K.TYPE_ALIGNMENT_CHANGED],
            id="alignment-pairs-with-its-own-kind",
        ),
        pytest.param(
            Change(K.STRUCT_FIELD_REMOVED, "demo::S::x", "dwarf", field_name="x"),
            Change(K.TYPE_FIELD_REMOVED, "demo::S", "header", field_name="x"),
            [K.TYPE_FIELD_REMOVED],
            id="field-removed-parent-type-with-same-field",
        ),
        pytest.param(
            Change(K.STRUCT_FIELD_REMOVED, "demo::S::x", "dwarf", field_name="x"),
            Change(K.TYPE_FIELD_REMOVED, "demo::S", "header", field_name="y"),
            [K.STRUCT_FIELD_REMOVED, K.TYPE_FIELD_REMOVED],
            id="field-removed-different-field-of-same-type-stays",
        ),
        pytest.param(
            Change(K.STRUCT_FIELD_OFFSET_CHANGED, "demo::S::x", "dwarf", "0", "4"),
            Change(K.TYPE_FIELD_OFFSET_CHANGED, "demo::S", "header", "0", "32"),
            [K.TYPE_FIELD_OFFSET_CHANGED],
            id="field-offset-parent-type-same-transition",
        ),
        pytest.param(
            Change(K.STRUCT_FIELD_OFFSET_CHANGED, "demo::S::x", "dwarf", "0", "4"),
            Change(K.TYPE_FIELD_OFFSET_CHANGED, "demo::S", "header", "0", "64"),
            [K.STRUCT_FIELD_OFFSET_CHANGED, K.TYPE_FIELD_OFFSET_CHANGED],
            id="field-offset-disagreeing-transition-stays-apart",
        ),
        pytest.param(
            Change(K.STRUCT_SIZE_CHANGED, "demo::S::Inner", "dwarf", "8", "16"),
            Change(K.TYPE_SIZE_CHANGED, "demo::S", "header", "64", "128"),
            [K.STRUCT_SIZE_CHANGED, K.TYPE_SIZE_CHANGED],
            id="nested-type-is-not-the-parent-of-its-size",
        ),
    ],
)
# The DWARF tier (STRUCT_*) reports bytes; the AST tier (TYPE_*) reports bits.
# cross_tier_transition converts the DWARF side, so the rows below state each
# side in its own unit.
def test_dwarf_copy_drops_only_for_a_matching_ast_finding(
    dwarf: Change, ast: Change, expected: list[K]
) -> None:
    assert [c.kind for c in deduplicate_ast_dwarf([dwarf, ast])] == expected


# ---------------------------------------------------------------------------
# Same-kind enum and exact-key collapse in deduplicate_ast_dwarf.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("first", "second", "winner"),
    [
        pytest.param(
            Change(K.ENUM_MEMBER_VALUE_CHANGED, "Color::RED", "no values here"),
            Change(K.ENUM_MEMBER_VALUE_CHANGED, "Color::RED", "with", "0", "1"),
            "with",
            id="valued-copy-beats-bare-copy",
        ),
        pytest.param(
            Change(K.ENUM_MEMBER_VALUE_CHANGED, "Color::RED", "with", "0", "1"),
            Change(K.ENUM_MEMBER_VALUE_CHANGED, "Color::RED", "no values here"),
            "with",
            id="valued-copy-wins-in-either-order",
        ),
        pytest.param(
            Change(K.ENUM_MEMBER_VALUE_CHANGED, "Color::RED", "short"),
            Change(K.ENUM_MEMBER_VALUE_CHANGED, "Color::RED", "a longer wording"),
            "a longer wording",
            id="longer-description-wins-without-values",
        ),
        pytest.param(
            Change(K.ENUM_MEMBER_VALUE_CHANGED, "Color::RED", "a longer wording"),
            Change(K.ENUM_MEMBER_VALUE_CHANGED, "Color::RED", "short"),
            "a longer wording",
            id="longer-description-wins-in-either-order",
        ),
        pytest.param(
            Change(K.ENUM_MEMBER_VALUE_CHANGED, "Color::RED", "same len A"),
            Change(K.ENUM_MEMBER_VALUE_CHANGED, "Color::RED", "same len B"),
            "same len A",
            id="equal-length-keeps-first-occurrence",
        ),
        pytest.param(
            Change(K.ENUM_MEMBER_VALUE_CHANGED, "Color::RED", "x", "0", "1"),
            Change(K.ENUM_MEMBER_VALUE_CHANGED, "Color::RED", "a much longer wording"),
            "x",
            id="valued-copy-beats-longer-bare-copy",
        ),
    ],
)
def test_same_kind_enum_copies_keep_the_better_evidenced_one(
    first: Change, second: Change, winner: str
) -> None:
    survivors = deduplicate_ast_dwarf([first, second])
    assert [c.description for c in survivors] == [winner]


def _member_entity(leaf: str) -> EntityId:
    return EntityId(
        scope=(),
        kind=EntityKind.VARIABLE,
        leaf_name=leaf,
        extra=("mangled", "_ZN3K_S3K_VE"),
    )


@pytest.mark.parametrize(
    ("first", "second", "expected_count"),
    [
        pytest.param(
            Change(K.FUNC_ADDED, "f_a", "added"),
            Change(K.FUNC_ADDED, "f_a", "added"),
            1,
            id="identical-exact-copies-collapse",
        ),
        pytest.param(
            Change(K.FUNC_ADDED, "f_a", "added"),
            Change(K.FUNC_ADDED, "f_a", "added again"),
            2,
            id="different-description-stays",
        ),
        pytest.param(
            Change(K.FUNC_ADDED, "f_a", "added", "1", "2"),
            Change(K.FUNC_ADDED, "f_a", "added", "1", "3"),
            2,
            id="different-new-value-stays",
        ),
        pytest.param(
            Change(K.CONSTANT_CHANGED, "K", "c", "1", "2", disambiguator="one"),
            Change(K.CONSTANT_CHANGED, "K", "c", "1", "2", disambiguator="two"),
            2,
            id="occurrence-kind-distinct-disambiguator-stays",
        ),
        pytest.param(
            Change(K.CONSTANT_CHANGED, "K", "c", "1", "2", disambiguator="one"),
            Change(K.CONSTANT_CHANGED, "K", "c", "1", "2", disambiguator="one"),
            1,
            id="occurrence-kind-same-disambiguator-collapses",
        ),
        pytest.param(
            Change(
                K.CONSTANT_CHANGED,
                "K",
                "c",
                "1",
                "2",
                entity_id=_member_entity("K"),
            ),
            Change(
                K.CONSTANT_CHANGED,
                "K",
                "c",
                "1",
                "2",
                entity_id=_member_entity("J"),
            ),
            2,
            id="occurrence-kind-distinct-entity-stays",
        ),
    ],
)
def test_exact_copies_collapse_on_the_documented_key(
    first: Change, second: Change, expected_count: int
) -> None:
    assert len(deduplicate_ast_dwarf([first, second])) == expected_count


def test_dwarf_only_enum_bridges_its_bare_name_too() -> None:
    # An enum that only DWARF saw: no header-tier EnumType, so its bare-name
    # bridge comes from the DWARF enum table's own qualified keys.
    snap = AbiSnapshot(
        library="lib.so",
        version="1",
        dwarf=DwarfMetadata(
            has_dwarf=True,
            enums={
                "ns::Color": EnumInfo(
                    name="ns::Color",
                    underlying_byte_size=4,
                    members={"RED": 0},
                ),
                "Global": EnumInfo(
                    name="Global",
                    underlying_byte_size=4,
                    members={"ONE": 1},
                ),
            },
        ),
    )
    # Only the bare spelling is registered: a DWARF-only symbol is already
    # qualified, so it needs no canonical entry of its own.
    assert enum_canonical_names(snap) == {"Color": "ns::Color"}


def test_dwarf_only_enum_findings_collapse_with_the_header_spelling() -> None:
    snap = AbiSnapshot(
        library="lib.so",
        version="1",
        dwarf=DwarfMetadata(
            has_dwarf=True,
            enums={
                "ns::Color": EnumInfo(
                    name="ns::Color",
                    underlying_byte_size=4,
                    members={"RED": 0},
                ),
            },
        ),
    )
    header = Change(K.ENUM_MEMBER_VALUE_CHANGED, "Color::RED", "header tier", "0", "1")
    dwarf = Change(
        K.ENUM_MEMBER_VALUE_CHANGED, "ns::Color::RED", "dwarf tier", "0", "1"
    )
    survivors = deduplicate_cross_detector([header, dwarf], snap, snap)
    assert [c.qualified_name for c in survivors] == ["ns::Color::RED"]


@pytest.mark.parametrize(
    ("first", "second", "winner"),
    [
        pytest.param(
            Change(K.ENUM_MEMBER_VALUE_CHANGED, "Color::RED", "x", "0", None),
            Change(K.ENUM_MEMBER_VALUE_CHANGED, "Color::RED", "a much longer wording"),
            "x",
            id="old-value-alone-is-evidence",
        ),
        pytest.param(
            Change(K.ENUM_MEMBER_VALUE_CHANGED, "Color::RED", "x", None, "1"),
            Change(K.ENUM_MEMBER_VALUE_CHANGED, "Color::RED", "a much longer wording"),
            "x",
            id="new-value-alone-is-evidence",
        ),
    ],
)
def test_one_sided_enum_value_counts_as_evidence(
    first: Change, second: Change, winner: str
) -> None:
    survivors = deduplicate_ast_dwarf([first, second])
    assert [c.description for c in survivors] == [winner]


def test_ast_side_qualified_spelling_pairs_a_bare_header_type_with_its_dwarf_copy() -> (
    None
):
    # The header tier names the record bare and carries its qualified spelling;
    # the DWARF copy is qualified. Only the qualified spelling makes them pair.
    header = Change(K.TYPE_SIZE_CHANGED, "S", "header tier", "64", "128")
    header.qualified_name = "demo::S"
    dwarf = Change(K.STRUCT_SIZE_CHANGED, "demo::S", "dwarf tier", "8", "16")
    assert [c.kind for c in deduplicate_ast_dwarf([dwarf, header])] == [
        K.TYPE_SIZE_CHANGED
    ]


def test_enum_bridge_runs_with_only_one_snapshot_supplied() -> None:
    # A caller that has the old snapshot alone still gets the bare-to-qualified
    # bridge: the dedup must not need both sides to be present.
    snap = _snap_with_enums(("Color", "ns::Color"))
    header = Change(K.ENUM_MEMBER_REMOVED, "Color::RED", "header tier")
    dwarf = Change(K.ENUM_MEMBER_REMOVED, "ns::Color::RED", "dwarf tier")
    assert len(deduplicate_cross_detector([header, dwarf], snap, None)) == 1
    assert len(deduplicate_cross_detector([header, dwarf], None, snap)) == 1


def test_variable_copy_qualifies_by_its_entity_scope() -> None:
    # A C-linkage name inside a namespace: only the entity id knows the scope,
    # the mangled-or-leaf spelling alone is just "g_var".
    entity = EntityId(
        scope=(Namespace(name="ns"),),
        kind=EntityKind.VARIABLE,
        leaf_name="g_var",
    )
    constant = Change(K.CONSTANT_CHANGED, "ns::g_var", "constant", "1", "2")
    variable = Change(
        K.VAR_VALUE_CHANGED, "g_var", "variable", "1", "2", entity_id=entity
    )
    assert [c.kind for c in deduplicate_cross_detector([constant, variable])] == [
        K.CONSTANT_CHANGED
    ]
