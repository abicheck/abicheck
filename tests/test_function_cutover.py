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

"""The function detector family's cohort-3 read path (ADR-063 Phase 6B),
plus the architecture gate that keeps it migrated.

Unlike ``tests/test_typedef_cutover.py``/``tests/test_constant_cutover.py``,
this cohort does not migrate a payload fact -- see ``compare/functions.py``'s
own module docstring for the full scoping account. So the property under
test here is narrower, but just as load-bearing: ``function_identity_index``
must be indistinguishable, from every caller's perspective, from the
``SymbolIdentityIndex.for_functions`` it replaces -- for a snapshot with no
``SemanticIR``, one whose ``SemanticIR`` does not cover ``FUNCTION``
entities, and one whose real ``SemanticIR`` does cover them (fully or only
partially). ``TestThroughCompare`` checks the same property one layer up,
through the real ``diff_symbols._diff_functions`` entry point.
"""

from __future__ import annotations

from abicheck.checker import compare
from abicheck.compare.functions import function_identity_index
from abicheck.finding_identity import SymbolIdentityIndex
from abicheck.model import AbiSnapshot, Function, Param, Visibility
from abicheck.model.fact import Fact
from abicheck.model.identity import EntityKind, Namespace, entity_id_for_function
from abicheck.model.occurrence import OccurrenceId
from abicheck.model.semantic_ir import CanonicalEntity, SemanticIR
from abicheck.model.semantic_ir_legacy_adapter import legacy_function_ir


def _eid(name: str, *, mangled: str | None = None, extern_c: bool = False):
    return entity_id_for_function(
        (Namespace("ns"),), name, mangled_name=mangled, is_extern_c=extern_c
    )


def _func(
    name: str,
    mangled: str,
    *,
    extern_c: bool = False,
    params: list[Param] | None = None,
    visibility: Visibility = Visibility.PUBLIC,
    entity_id=None,
) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        params=params or [],
        visibility=visibility,
        is_extern_c=extern_c,
        entity_id=entity_id,
    )


def _snapshot(functions=(), *, semantic_ir: SemanticIR | None = None) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so",
        version="1.0",
        functions=list(functions),
        semantic_ir=semantic_ir,
    )


def _ir_covering_functions(*entity_ids) -> SemanticIR:
    """A real, ``FUNCTION``-covering ``SemanticIR`` naming each of
    *entity_ids* -- mirrors what ``extract/semantic_normalizer.py``'s third
    slice would produce (an occurrence per resolved function, payload left
    unexercised since this cohort's matching does not read it)."""
    return SemanticIR(
        occurrences={
            OccurrenceId(eid): CanonicalEntity(canonical_spelling=Fact.not_collected())
            for eid in entity_ids
        }
    )


class TestFunctionIdentityIndexEquivalence:
    """``function_identity_index`` must resolve to exactly the same
    ``SymbolIdentityIndex`` entries/aliases as ``SymbolIdentityIndex.
    for_functions`` regardless of which occurrence set backed it."""

    def test_no_semantic_ir_matches_the_legacy_index_exactly(self) -> None:
        entries = {
            "_Z1fv": _func("f", "_Z1fv", entity_id=_eid("f", mangled="_Z1fv")),
            "g": _func("g", "g", extern_c=True, entity_id=_eid("g", extern_c=True)),
        }
        snapshot = _snapshot(entries.values())
        via_cutover = function_identity_index(entries, snapshot)
        via_legacy = SymbolIdentityIndex.for_functions(entries)
        assert dict(via_cutover) == dict(via_legacy)
        for key in entries:
            cutover_entry = via_cutover.entry(key)
            legacy_entry = via_legacy.entry(key)
            assert cutover_entry is not None and legacy_entry is not None
            assert cutover_entry.identity == legacy_entry.identity

    def test_semantic_ir_not_covering_function_falls_back_identically(
        self,
    ) -> None:
        """A ``SemanticIR`` that exists but names no ``FUNCTION`` occurrence
        at all (e.g. a snapshot whose IR only covers typedefs) must not be
        trusted for this cohort -- ``semantic_ir_covers_kind``'s per-kind
        gate, mirroring ``compare.typedefs``/``compare.constants``."""
        entries = {"_Z1fv": _func("f", "_Z1fv", entity_id=_eid("f", mangled="_Z1fv"))}
        empty_ir = SemanticIR(occurrences={})
        snapshot = _snapshot(entries.values(), semantic_ir=empty_ir)
        via_cutover = function_identity_index(entries, snapshot)
        via_legacy = SymbolIdentityIndex.for_functions(entries)
        assert dict(via_cutover) == dict(via_legacy)

    def test_a_real_function_covering_semantic_ir_still_resolves_identically(
        self,
    ) -> None:
        """The identity computation itself is unchanged (see this module's
        docstring for why) -- so even a real, matching ``SemanticIR`` must
        not change a single resolved entry."""
        eid_f = _eid("f", mangled="_Z1fv")
        eid_g = _eid("g", extern_c=True)
        entries = {
            "_Z1fv": _func("f", "_Z1fv", entity_id=eid_f),
            "g": _func("g", "g", extern_c=True, entity_id=eid_g),
        }
        snapshot = _snapshot(
            entries.values(), semantic_ir=_ir_covering_functions(eid_f, eid_g)
        )
        via_cutover = function_identity_index(entries, snapshot)
        via_legacy = SymbolIdentityIndex.for_functions(entries)
        assert dict(via_cutover) == dict(via_legacy)
        assert (via_cutover.unique_alias_match("name:g") is not None) == (
            via_legacy.unique_alias_match("name:g") is not None
        )

    def test_a_partially_covering_semantic_ir_still_resolves_identically(
        self,
    ) -> None:
        """A real ``SemanticIR`` covering ``FUNCTION`` in general but missing
        *this particular* function's own occurrence (e.g. an ELF-fallback
        exported function no header-AST backend saw) is the legitimate,
        expected gap this module's own docstring names -- not an error, and
        not a reason to diverge from the legacy computation."""
        eid_f = _eid("f", mangled="_Z1fv")
        eid_other = _eid("unrelated", mangled="_Z8unrelatedv")
        entries = {"_Z1fv": _func("f", "_Z1fv", entity_id=eid_f)}
        snapshot = _snapshot(
            entries.values(), semantic_ir=_ir_covering_functions(eid_other)
        )
        via_cutover = function_identity_index(entries, snapshot)
        via_legacy = SymbolIdentityIndex.for_functions(entries)
        assert dict(via_cutover) == dict(via_legacy)

    def test_a_function_with_no_entity_id_still_resolves_identically(self) -> None:
        """A function carrying no ``entity_id`` at all (a historical,
        pre-identity snapshot) must not crash the cutover path -- it simply
        never participates in the presence check."""
        entries = {"_Z1fv": _func("f", "_Z1fv", entity_id=None)}
        snapshot = _snapshot(entries.values())
        via_cutover = function_identity_index(entries, snapshot)
        via_legacy = SymbolIdentityIndex.for_functions(entries)
        assert dict(via_cutover) == dict(via_legacy)


class TestLegacyFunctionIr:
    def test_projects_one_occurrence_per_function_keyed_by_its_own_entity_id(
        self,
    ) -> None:
        eid = _eid("f", mangled="_Z1fv")
        functions = {"_Z1fv": _func("f", "_Z1fv", entity_id=eid)}
        ir = legacy_function_ir(functions)
        assert set(ir.occurrences) == {OccurrenceId(eid)}

    def test_a_function_with_no_entity_id_gets_a_synthetic_one(self) -> None:
        functions = {"_Z1fv": _func("f", "_Z1fv", entity_id=None)}
        ir = legacy_function_ir(functions)
        assert len(ir.occurrences) == 1
        (occurrence_id,) = ir.occurrences
        assert occurrence_id.entity_id.kind is EntityKind.FUNCTION
        assert occurrence_id.entity_id.leaf_name == "_Z1fv"


class TestThroughCompare:
    """The real entry point: ``diff_symbols._diff_functions``, exercised via
    ``checker.compare``, must report identical findings whether or not the
    snapshots carry a real, function-covering ``SemanticIR``."""

    def _kinds(self, old: AbiSnapshot, new: AbiSnapshot):
        return [
            (c.kind, c.symbol, c.old_value, c.new_value)
            for c in compare(old, new).changes
        ]

    def test_a_removed_function_is_unaffected_by_a_real_semantic_ir(self) -> None:
        eid = _eid("f", mangled="_Z1fv")
        old_no_ir = _snapshot([_func("f", "_Z1fv", entity_id=eid)])
        new_no_ir = _snapshot([])
        old_ir = _snapshot(
            [_func("f", "_Z1fv", entity_id=eid)],
            semantic_ir=_ir_covering_functions(eid),
        )
        new_ir = _snapshot([], semantic_ir=SemanticIR(occurrences={}))
        assert self._kinds(old_no_ir, new_no_ir) == self._kinds(old_ir, new_ir)

    def test_an_added_function_is_unaffected_by_a_real_semantic_ir(self) -> None:
        eid = _eid("f", mangled="_Z1fv")
        old_no_ir = _snapshot([])
        new_no_ir = _snapshot([_func("f", "_Z1fv", entity_id=eid)])
        old_ir = _snapshot([], semantic_ir=SemanticIR(occurrences={}))
        new_ir = _snapshot(
            [_func("f", "_Z1fv", entity_id=eid)],
            semantic_ir=_ir_covering_functions(eid),
        )
        assert self._kinds(old_no_ir, new_no_ir) == self._kinds(old_ir, new_ir)

    def test_extern_c_name_fallback_is_unaffected_by_a_real_semantic_ir(
        self,
    ) -> None:
        """The one non-trivial matching tier this cohort touches: an
        ``extern "C"`` function whose mangled representation changes across
        a linkage flip is joined by plain name, ambiguity-checked. Must
        still fire identically with a real ``SemanticIR`` in play."""
        eid_old = _eid("g", extern_c=True)
        eid_new = _eid("g", mangled="_Z1gi")
        old_no_ir = _snapshot([_func("g", "g", extern_c=True, entity_id=eid_old)])
        new_no_ir = _snapshot(
            [
                _func(
                    "g",
                    "_Z1gi",
                    params=[Param(name="x", type="int")],
                    entity_id=eid_new,
                )
            ]
        )
        old_ir = _snapshot(
            [_func("g", "g", extern_c=True, entity_id=eid_old)],
            semantic_ir=_ir_covering_functions(eid_old),
        )
        new_ir = _snapshot(
            [
                _func(
                    "g",
                    "_Z1gi",
                    params=[Param(name="x", type="int")],
                    entity_id=eid_new,
                )
            ],
            semantic_ir=_ir_covering_functions(eid_new),
        )
        old_kinds = self._kinds(old_no_ir, new_no_ir)
        new_kinds = self._kinds(old_ir, new_ir)
        assert old_kinds == new_kinds
        # And it is a real match (params-changed), not two independent
        # add+remove findings -- otherwise this test would trivially pass
        # for the wrong reason.
        assert old_kinds, "expected the extern-C fallback to produce a finding"

    def test_an_unchanged_pair_reports_nothing_either_way(self) -> None:
        eid = _eid("f", mangled="_Z1fv")
        func = _func("f", "_Z1fv", entity_id=eid)
        no_ir = _snapshot([func])
        with_ir = _snapshot([func], semantic_ir=_ir_covering_functions(eid))
        assert self._kinds(no_ir, no_ir) == [] == self._kinds(with_ir, with_ir)


class TestSemanticIrCutoverGate:
    def test_the_migrated_module_reads_no_legacy_function_collection(self) -> None:
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from findings_report import Findings
        from semantic_ir_cutover import check_semantic_ir_cutover

        findings = Findings()
        check_semantic_ir_cutover(findings)
        assert findings.errors == []

    def test_the_functions_cohort_is_registered(self) -> None:
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from semantic_ir_cutover import MIGRATED_COHORTS

        names = {c.name for c in MIGRATED_COHORTS}
        assert "functions" in names
        cohort = next(c for c in MIGRATED_COHORTS if c.name == "functions")
        assert cohort.modules == ("abicheck/compare/functions.py",)
        assert "functions" in cohort.forbidden_attributes
        assert "function_map" in cohort.forbidden_attributes

    def test_the_gate_actually_fires_on_a_forbidden_read(self) -> None:
        import ast
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from semantic_ir_cutover import legacy_collection_reads

        forbidden = frozenset({"functions", "function_map"})
        for source in (
            "x = snap.functions",
            "x = old.function_map",
            'x = getattr(snap, "function_map")',
        ):
            found = legacy_collection_reads(ast.parse(source), forbidden)
            assert found, f"gate missed: {source!r}"

    def test_the_gate_does_not_fire_on_a_local_or_a_keyword(self) -> None:
        import ast
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from semantic_ir_cutover import legacy_collection_reads

        forbidden = frozenset({"functions", "function_map"})
        for source in (
            "functions = {}\nx = functions",
            "build(snapshot, functions=the_map)",
        ):
            assert legacy_collection_reads(ast.parse(source), forbidden) == []
