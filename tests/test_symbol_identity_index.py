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

"""ADR-049 Phase 2: the ambiguity-safe old/new join.

Covers `SymbolIdentityIndex` in isolation, and — through `checker.compare` —
that routing `diff_symbols.py`'s function and variable matching through it
preserves every documented matching rule: the `extern "C"` fallback and its
ambiguity guard, and the deliberate *absence* of a name fallback for
variables and for mangled C++ functions.
"""

from __future__ import annotations

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.finding_identity import (
    SymbolIdentityIndex,
    resolve_function_identity,
)
from abicheck.model import AbiSnapshot, Function, Param, Variable, Visibility


def _func(
    name: str,
    mangled: str,
    *,
    extern_c: bool = False,
    params: list[Param] | None = None,
    visibility: Visibility = Visibility.PUBLIC,
) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        params=params or [],
        visibility=visibility,
        is_extern_c=extern_c,
    )


def _var(name: str, mangled: str) -> Variable:
    return Variable(
        name=name, mangled=mangled, type="int", visibility=Visibility.PUBLIC
    )


def _snapshot(functions=(), variables=()) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so",
        version="1.0",
        functions=list(functions),
        variables=list(variables),
    )


def _kinds(old: AbiSnapshot, new: AbiSnapshot) -> list[ChangeKind]:
    return [c.kind for c in compare(old, new).changes]


class TestMappingInterface:
    def test_it_is_a_mapping_over_the_entries_own_keys(self):
        entries = {"_Z1fv": _func("f", "_Z1fv"), "g": _func("g", "g", extern_c=True)}
        index = SymbolIdentityIndex.for_functions(entries)
        assert len(index) == 2
        assert set(index) == {"_Z1fv", "g"}
        assert index["_Z1fv"].name == "f"
        assert "g" in index
        assert index.get("missing") is None

    def test_iteration_visits_each_declaration_once(self):
        # The alias index must not leak into iteration -- a detector loop over
        # .values() would otherwise emit each finding twice.
        index = SymbolIdentityIndex.for_functions({"_Z1fv": _func("f", "_Z1fv")})
        assert [f.name for f in index.values()] == ["f"]
        assert len(list(index.items())) == 1

    def test_an_alias_is_not_resolved_by_key_lookup(self):
        # Unlike TypeMap's bare-name alias: two differing mangled names are two
        # different exports, and healing that silently would hide a removal.
        index = SymbolIdentityIndex.for_functions({"_Z1fv": _func("f", "_Z1fv")})
        assert "f" not in index
        assert index.get("f") is None


class TestAmbiguitySafety:
    def test_a_unique_alias_resolves(self):
        index = SymbolIdentityIndex.for_functions({"_Z1fv": _func("f", "_Z1fv")})
        match = index.unique_alias_match("name:f")
        assert match is not None
        assert match.key == "_Z1fv"
        assert match.matched_on == "name:f"

    def test_an_absent_alias_resolves_to_nothing(self):
        index = SymbolIdentityIndex.for_functions({"_Z1fv": _func("f", "_Z1fv")})
        assert index.unique_alias_match("name:nope") is None

    def test_an_ambiguous_alias_resolves_to_nothing(self):
        # Two overloads share a display name: guessing between them is exactly
        # the mis-pairing this tier exists to refuse.
        index = SymbolIdentityIndex.for_functions(
            {
                "_Z1fi": _func("f", "_Z1fi", params=[Param(name="a", type="int")]),
                "_Z1fd": _func("f", "_Z1fd", params=[Param(name="a", type="double")]),
            }
        )
        assert index.unique_alias_match("name:f") is None
        assert len(index.alias_candidates("name:f")) == 2

    def test_alias_candidates_tells_absent_from_ambiguous(self):
        index = SymbolIdentityIndex.for_functions({"_Z1fv": _func("f", "_Z1fv")})
        assert index.alias_candidates("name:nope") == []
        assert len(index.alias_candidates("name:f")) == 1

    def test_a_predicate_narrows_before_counting(self):
        index = SymbolIdentityIndex.for_functions(
            {
                "f": _func("f", "f", extern_c=True),
                "_Z1fv": _func("f", "_Z1fv"),
            }
        )
        assert index.unique_alias_match("name:f") is None
        narrowed = index.unique_alias_match("name:f", where=lambda fn: fn.is_extern_c)
        assert narrowed is not None
        assert narrowed.key == "f"

    def test_the_entry_record_carries_the_resolved_identity(self):
        fn = _func("f", "_Z1fv")
        index = SymbolIdentityIndex.for_functions({"_Z1fv": fn})
        entry = index.entry("_Z1fv")
        assert entry is not None
        assert entry.identity == resolve_function_identity(fn)
        assert entry.matched_on == "key"
        assert index.entry("missing") is None


class TestFunctionMatchingThroughTheIndex:
    """The rules `_match_old_function` documents, end to end."""

    def test_an_exact_key_pair_is_matched_not_removed(self):
        old = _snapshot([_func("f", "_Z1fv")])
        new = _snapshot([_func("f", "_Z1fv")])
        assert _kinds(old, new) == []

    def test_extern_c_peer_matches_by_name_despite_a_differing_key(self):
        # The C/C++ compilation-mode mismatch the fallback exists for.
        old = _snapshot([_func("f", "f", extern_c=True)])
        new = _snapshot([_func("f", "_Z1fv")])
        kinds = _kinds(old, new)
        assert ChangeKind.FUNC_REMOVED not in kinds
        assert ChangeKind.FUNC_ADDED not in kinds

    def test_an_ambiguous_name_is_not_matched(self):
        # Two same-named new candidates: the old export must stay a removal
        # rather than be paired with an arbitrary one of them.
        old = _snapshot([_func("f", "f", extern_c=True)])
        new = _snapshot(
            [
                _func("f", "_Z1fi", params=[Param(name="a", type="int")]),
                _func("f", "_Z1fd", params=[Param(name="a", type="double")]),
            ]
        )
        assert ChangeKind.FUNC_REMOVED in _kinds(old, new)

    def test_a_cxx_old_side_does_not_match_a_cxx_peer_by_name(self):
        # A changed mangled name on a C++ function IS a removal + addition:
        # the old export is genuinely gone. Only an extern-C candidate is
        # eligible for the name fallback.
        old = _snapshot([_func("f", "_Z1fv")])
        new = _snapshot([_func("f", "_Z1fi", params=[Param(name="a", type="int")])])
        kinds = _kinds(old, new)
        assert ChangeKind.FUNC_REMOVED in kinds
        assert ChangeKind.FUNC_ADDED in kinds

    def test_a_name_matched_peer_is_not_also_reported_as_added(self):
        old = _snapshot([_func("f", "f", extern_c=True)])
        new = _snapshot([_func("f", "_Z1fv")])
        assert ChangeKind.FUNC_ADDED not in _kinds(old, new)

    def test_a_genuinely_removed_function_is_still_reported(self):
        old = _snapshot([_func("gone", "_Z4gonev")])
        new = _snapshot([])
        assert ChangeKind.FUNC_REMOVED in _kinds(old, new)

    def test_a_genuinely_added_function_is_still_reported(self):
        old = _snapshot([])
        new = _snapshot([_func("fresh", "_Z5freshv")])
        assert ChangeKind.FUNC_ADDED in _kinds(old, new)


class TestVariableMatchingThroughTheIndex:
    def test_an_exact_key_pair_is_matched(self):
        old = _snapshot(variables=[_var("counter", "counter")])
        new = _snapshot(variables=[_var("counter", "counter")])
        assert _kinds(old, new) == []

    def test_no_name_fallback_is_enabled_for_variables(self):
        # Deliberate: a variable's key IS its exported symbol, so a differing
        # key is a different export. Reporting removal + addition is correct,
        # and a display-name join would hide the removal.
        old = _snapshot(variables=[_var("counter", "_ZN2ns7counterE")])
        new = _snapshot(variables=[_var("counter", "_ZN3ns27counterE")])
        kinds = _kinds(old, new)
        assert ChangeKind.VAR_REMOVED in kinds
        assert ChangeKind.VAR_ADDED in kinds

    def test_a_removed_variable_is_still_reported(self):
        old = _snapshot(variables=[_var("gone", "gone")])
        new = _snapshot(variables=[])
        assert ChangeKind.VAR_REMOVED in _kinds(old, new)


class TestIndexConstruction:
    @pytest.mark.parametrize("factory", ["for_functions", "for_variables"])
    def test_an_empty_map_builds_an_empty_index(self, factory):
        index = getattr(SymbolIdentityIndex, factory)({})
        assert len(index) == 0
        assert index.unique_alias_match("name:anything") is None

    def test_variables_index_resolves_variable_identities(self):
        index = SymbolIdentityIndex.for_variables(
            {"counter": _var("counter", "counter")}
        )
        entry = index.entry("counter")
        assert entry is not None
        assert entry.identity.aliases
        assert index.unique_alias_match("name:counter") is not None
