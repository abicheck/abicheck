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

"""The bug class ``evidence.export_presence_as_declaration_presence``.

Three facts used to be one ``Visibility`` value (see
``abicheck/model/surface_facts.py`` and the ``Visibility.PUBLIC`` entry in
``docs/contribute/known-gaps.md``), so a detector asking "is this
declared?" was answered with "is this exported?".

These tests state the *class*, not the one reported input, by enumerating
the whole small domain of the three facts rather than asserting one
scenario: every combination of the three tri-state facts is generated, and
the invariants below are checked against an oracle that is independent of
the implementation (the constructed facts themselves, not
``surface_facts``' own predicates).
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.checker import Verdict, compare
from abicheck.extract.surface_fact_producers import debug_info_surface_facts
from abicheck.model import (
    AbiSnapshot,
    Fact,
    FactStatus,
    Function,
    ScopeOrigin,
    Variable,
    Visibility,
    binary_exported,
    declaration_confirmed_absent,
    declared_in_headers,
    in_public_contract,
    in_public_surface,
    in_source_declaration_index,
    is_abi_visible,
    is_binary_exported,
    is_confirmed_false,
    is_confirmed_true,
    is_export_table_only_record,
    is_header_declared,
    is_public_export,
    is_unknown,
    surface_fact_summary,
)
from abicheck.model.change_catalog.kinds import ChangeKind

#: Every way a producer can leave one of the three facts. "Unknown" is
#: represented by all four of its real statuses, not just one, so an
#: invariant cannot accidentally hold for ``NOT_COLLECTED`` alone.
_TRUE = Fact.present(True)
_FALSE = Fact.present(False)
_UNKNOWNS = (
    None,
    Fact.not_collected("no headers parsed"),
    Fact.unsupported("producer cannot answer"),
    Fact.failed("extractor errored"),
    Fact.not_applicable(),
)
_STATES = (_TRUE, _FALSE, *_UNKNOWNS)


def _fn(name: str = "foo", mangled: str = "_Z3foov", **facts: object) -> Function:
    return Function(name=name, mangled=mangled, return_type="void", **facts)  # type: ignore[arg-type]


def _snap(version: str, *functions: Function, **kw: object) -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so",
        version=version,
        functions=list(functions),
        from_headers=True,
        **kw,  # type: ignore[arg-type]
    )


def _expected_tri(fact: Fact[bool] | None) -> str:
    """The oracle: derived from the fact this test *constructed*, never from
    the module under test's own predicates."""
    if fact is None:
        return "unknown"
    if fact.status in (FactStatus.PRESENT, FactStatus.PARTIAL):
        return "true" if fact.value is True else "false"
    return "unknown"


class TestThreeFactsStayThreeFacts:
    """Each fact answers only its own question, for every combination."""

    @pytest.mark.parametrize(
        ("declared", "contract", "exported"), list(itertools.product(_STATES, repeat=3))
    )
    def test_each_accessor_returns_only_its_own_fact(
        self,
        declared: Fact[bool] | None,
        contract: Fact[bool] | None,
        exported: Fact[bool] | None,
    ) -> None:
        fn = _fn(
            declared_in_headers_fact=declared,
            in_public_contract_fact=contract,
            binary_exported_fact=exported,
        )
        summary = surface_fact_summary(fn)
        # A fact left unset falls back to the legacy `visibility` bridge, so
        # only the explicitly-set ones are pinned here; the bridge itself is
        # the subject of TestLegacyBridge below.
        if declared is not None:
            assert summary["declared_in_headers"] == _expected_tri(declared)
        if contract is not None:
            assert summary["in_public_contract"] == _expected_tri(contract)
        if exported is not None:
            assert summary["binary_exported"] == _expected_tri(exported)

    @pytest.mark.parametrize("exported", _STATES)
    def test_export_evidence_never_changes_the_declaration_answer(
        self, exported: Fact[bool] | None
    ) -> None:
        """The heart of the class: (c) must not move (a)."""
        fn = _fn(
            declared_in_headers_fact=_TRUE,
            in_public_contract_fact=_TRUE,
            binary_exported_fact=exported,
        )
        assert surface_fact_summary(fn)["declared_in_headers"] == "true"
        assert in_source_declaration_index(fn)

    @pytest.mark.parametrize("unknown", _UNKNOWNS)
    def test_unknown_is_never_a_negative_claim(
        self, unknown: Fact[bool] | None
    ) -> None:
        for field_name, accessor in (
            ("declared_in_headers_fact", declared_in_headers),
            ("in_public_contract_fact", in_public_contract),
            ("binary_exported_fact", binary_exported),
        ):
            # A headerless/binary-less snapshot: no legacy header provenance
            # either, so the bridge cannot manufacture a positive.
            fn = _fn(visibility=Visibility.ELF_ONLY, **{field_name: unknown})
            fact = accessor(fn)
            assert is_unknown(fact) or fact.value is not None
            if unknown is not None:
                assert is_unknown(fact), (field_name, unknown)


class TestExportLostWhileDeclarationRemains:
    """(1) An export lost while the declaration survives is never reported
    as a source-declaration removal."""

    @pytest.mark.parametrize(
        ("old_exported", "new_exported"),
        [
            (_TRUE, _FALSE),
            (_TRUE, Fact.not_collected("stripped binary")),
            (Fact.partial(True), _FALSE),
        ],
    )
    def test_no_removal_when_the_declaration_is_unchanged(
        self, old_exported: Fact[bool], new_exported: Fact[bool]
    ) -> None:
        declared = {
            "declared_in_headers_fact": _TRUE,
            "in_public_contract_fact": _TRUE,
        }
        old = _snap("1.0", _fn(**declared, binary_exported_fact=old_exported))
        new = _snap("2.0", _fn(**declared, binary_exported_fact=new_exported))
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.FUNC_REMOVED not in kinds
        assert ChangeKind.FUNC_REMOVED_ELF_ONLY not in kinds
        assert ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT not in kinds

    def test_the_export_change_is_still_reported_on_its_own_axis(self) -> None:
        """Not reporting a *removal* is not the same as ignoring the change:
        a disappearing export can still break an already-linked consumer."""
        declared = {
            "declared_in_headers_fact": _TRUE,
            "in_public_contract_fact": _TRUE,
        }
        old = _snap("1.0", _fn(**declared, binary_exported_fact=_TRUE))
        new = _snap("2.0", _fn(**declared, binary_exported_fact=_FALSE))
        changes = compare(old, new).changes
        visibility_changes = [
            c for c in changes if c.kind is ChangeKind.FUNC_VISIBILITY_CHANGED
        ]
        assert visibility_changes, [c.kind for c in changes]
        assert visibility_changes[0].surface_facts == {
            "declared_in_headers": "true",
            "in_public_contract": "true",
            "binary_exported": "false",
        }

    @pytest.mark.parametrize("exported", [_TRUE, _FALSE, *_UNKNOWNS])
    def test_a_real_declaration_removal_is_still_reported(
        self, exported: Fact[bool] | None
    ) -> None:
        """The fix must not silence the finding it is meant to keep honest:
        whatever the export evidence says, a declaration that is gone from
        the new side is still a removal."""
        old = _snap(
            "1.0",
            _fn(
                declared_in_headers_fact=_TRUE,
                in_public_contract_fact=_TRUE,
                binary_exported_fact=exported,
            ),
            _fn(name="keep", mangled="_Z4keepv"),
        )
        new = _snap("2.0", _fn(name="keep", mangled="_Z4keepv"))
        kinds = {c.kind for c in compare(old, new).changes}
        assert kinds & {ChangeKind.FUNC_REMOVED, ChangeKind.FUNC_REMOVED_ELF_ONLY}


class TestExportLossIsDetectedForEveryDeclarationKind:
    """The export axis is not a function-only axis.

    Registered because the first revision of this split implemented the
    transition detector for functions only: a variable whose export vanished
    while its declaration stayed then matched on both sides and produced *no
    finding at all*, trading one wrong finding (a source removal) for a
    missing one (a real binary break). Codex review, P1.
    """

    @staticmethod
    def _decl(owner: str, exported: Fact[bool] | None) -> Function | Variable:
        """One declared, promised declaration of either kind, whose only
        variable is the export fact."""
        facts: dict[str, object] = {
            "declared_in_headers_fact": _TRUE,
            "in_public_contract_fact": _TRUE,
            "binary_exported_fact": exported,
        }
        if owner == "variable":
            return Variable(
                name="g_registry",
                mangled="_Z10g_registry",
                type="int",
                **facts,  # type: ignore[arg-type]
            )
        return _fn(**facts)

    @staticmethod
    def _pair(owner: str, old_decl: object, new_decl: object) -> tuple[object, object]:
        old_snap, new_snap = _snap("1.0"), _snap("2.0")
        for snap, decl in ((old_snap, old_decl), (new_snap, new_decl)):
            if owner == "variable":
                snap.variables = [decl]  # type: ignore[list-item]
            else:
                snap.functions = [decl]  # type: ignore[list-item]
        return old_snap, new_snap

    @pytest.mark.parametrize(
        ("owner", "kind"),
        [
            ("function", ChangeKind.FUNC_VISIBILITY_CHANGED),
            ("variable", ChangeKind.VAR_VISIBILITY_CHANGED),
        ],
    )
    def test_a_lost_export_on_a_surviving_declaration_is_reported(
        self, owner: str, kind: ChangeKind
    ) -> None:
        old_snap, new_snap = self._pair(
            owner, self._decl(owner, _TRUE), self._decl(owner, _FALSE)
        )
        changes = compare(old_snap, new_snap).changes  # type: ignore[arg-type]
        kinds = {c.kind for c in changes}
        assert kind in kinds, [c.kind for c in changes]
        # And never as a removal: the declaration did not go anywhere.
        assert not kinds & {
            ChangeKind.FUNC_REMOVED,
            ChangeKind.FUNC_REMOVED_ELF_ONLY,
            ChangeKind.VAR_REMOVED,
        }
        stamped = [c for c in changes if c.kind is kind]
        assert stamped[0].surface_facts == {
            "declared_in_headers": "true",
            "in_public_contract": "true",
            "binary_exported": "false",
        }

    @pytest.mark.parametrize("unknown", [f for f in _UNKNOWNS if f is not None])
    @pytest.mark.parametrize("owner", ["function", "variable"])
    def test_unknown_new_side_evidence_is_never_an_observed_transition(
        self, owner: str, unknown: Fact[bool]
    ) -> None:
        """ "Exported before, unknown now" is a gap in this run's evidence, not
        an observed transition, and must never be rendered as one."""
        old_snap, new_snap = self._pair(
            owner, self._decl(owner, _TRUE), self._decl(owner, unknown)
        )
        kinds = {c.kind for c in compare(old_snap, new_snap).changes}  # type: ignore[arg-type]
        assert ChangeKind.FUNC_VISIBILITY_CHANGED not in kinds
        assert ChangeKind.VAR_VISIBILITY_CHANGED not in kinds


class TestExportTableOnlyRecordsAreNotSourceDeclarations:
    """An export-table stub has no source declaration to belong to.

    Its fact (a) is *unknown* on a headerless dump, not false, so the
    "unknown keeps it in" rule of the source-declaration population would
    admit it -- feeding raw mangled export entries to source-level
    detectors, which made a symbols-only comparison report an
    inline-namespace version bump with no header evidence behind it (Codex
    review, P2). See ``is_export_table_only_record``.
    """

    def test_export_only_entries_are_excluded(self) -> None:
        from abicheck.extract.export_symbol_identity import (
            itanium_export_function,
            itanium_export_variable,
        )

        for decl in (
            itanium_export_function("_ZN3lib2v13fooEv"),
            itanium_export_variable("_ZN3lib2v13barE"),
        ):
            # The premise the exclusion has to survive: (a) is unknown here,
            # which is exactly why the fact alone cannot carry this.
            assert surface_fact_summary(decl)["declared_in_headers"] == "unknown"
            assert not in_source_declaration_index(decl)

    def test_a_symbols_only_namespace_rename_reports_no_source_finding(self) -> None:
        from abicheck.extract.export_symbol_identity import itanium_export_function

        old = _snap("1.0", itanium_export_function("_ZN3lib2v13fooEv"))
        new = _snap("2.0", itanium_export_function("_ZN3lib2v23fooEv"))
        old.elf_only_mode = new.elf_only_mode = True
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.INLINE_NAMESPACE_VERSION_BUMPED not in kinds

    def test_a_header_declared_entity_is_still_included(self) -> None:
        """The exclusion is about the record's producer, not about (a) being
        unknown -- a DWARF-derived declaration also has (a) unknown and must
        stay in the population."""
        from abicheck.extract.surface_fact_producers import debug_info_surface_facts

        dwarf_decl = _fn(**debug_info_surface_facts(exported=True))  # type: ignore[arg-type]
        assert surface_fact_summary(dwarf_decl)["declared_in_headers"] == "unknown"
        assert in_source_declaration_index(dwarf_decl)


class TestEvidenceOnTheSideThatHasIt:
    """A declaration with no symbol *by construction* must not be judged by
    its own export fact.

    Three findings from one review round shared this root: once the export
    fact became honest (a `= delete`d function has no symbol, so it is a
    confirmed ``False``), every filter that asked only the *new* side stopped
    admitting exactly the declarations it existed to report.
    """

    @staticmethod
    def _deleted_pair() -> tuple[object, object]:
        old = _snap(
            "1.0",
            _fn(**debug_info_surface_facts(exported=True)),  # type: ignore[arg-type]
        )
        new = _snap(
            "2.0",
            _fn(
                is_deleted=True,
                deleted_from_dwarf=True,
                **debug_info_surface_facts(exported=False),  # type: ignore[arg-type]
            ),
        )
        return old, new

    def test_a_deleted_dwarf_function_is_still_reported_as_deleted(self) -> None:
        old, new = self._deleted_pair()
        kinds = [c.kind for c in compare(old, new).changes]  # type: ignore[arg-type]
        assert ChangeKind.FUNC_DELETED_DWARF in kinds, kinds

    def test_a_deleted_function_is_reported_exactly_once(self) -> None:
        """Not as a deletion *and* a visibility change: the removal path
        defers to the deletion detector, and both must agree on when."""
        old, new = self._deleted_pair()
        kinds = [c.kind for c in compare(old, new).changes]  # type: ignore[arg-type]
        assert kinds.count(ChangeKind.FUNC_DELETED_DWARF) == 1
        assert ChangeKind.FUNC_VISIBILITY_CHANGED not in kinds
        assert ChangeKind.FUNC_REMOVED not in kinds

    def test_the_legacy_path_reports_the_same_single_finding(self) -> None:
        """The oracle for the two assertions above: a pre-split snapshot pair,
        whose behaviour this change must not alter."""
        old = _snap("1.0", _fn())
        new = _snap("2.0", _fn(is_deleted=True, deleted_from_dwarf=True))
        assert [c.kind for c in compare(old, new).changes] == [
            ChangeKind.FUNC_DELETED_DWARF
        ]

    def test_unexported_debug_only_records_stay_out_of_source_indexes(self) -> None:
        """A debug-info-only record with neither header nor export evidence is
        not a source declaration -- admitting it let two unexported internal
        functions report an inline-namespace version bump."""
        unexported = _fn(**debug_info_surface_facts(exported=False))  # type: ignore[arg-type]
        summary = surface_fact_summary(unexported)
        assert summary["declared_in_headers"] == "unknown"
        assert summary["in_public_contract"] == "unknown"
        assert not in_source_declaration_index(unexported)
        # ... while the exported sibling, which legacy admitted, still is.
        assert in_source_declaration_index(
            _fn(**debug_info_surface_facts(exported=True))  # type: ignore[arg-type]
        )

    def test_a_mangling_churn_heuristic_counts_only_confirmed_exports(self) -> None:
        """`GLIBCXX_DUAL_ABI_FLIP_DETECTED` diagnoses a mangled-symbol change
        across two export tables, so a promised-but-unexported declaration has
        no symbol in either table to have churned."""
        promised = {
            "declared_in_headers_fact": _TRUE,
            "in_public_contract_fact": _TRUE,
            "binary_exported_fact": _FALSE,
        }
        old = _snap(
            "1.0",
            *[
                _fn(
                    name=f"f{i}",
                    mangled=f"_ZNSt7__cxx1112basic_stringE{i}",
                    **promised,  # type: ignore[arg-type]
                )
                for i in range(6)
            ],
        )
        new = _snap(
            "2.0",
            *[
                _fn(
                    name=f"f{i}",
                    mangled=f"_ZNSt12basic_stringE{i}",
                    **promised,  # type: ignore[arg-type]
                )
                for i in range(6)
            ],
        )
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.GLIBCXX_DUAL_ABI_FLIP_DETECTED not in kinds


class TestTheQuestionDecidesTheFact:
    """Each consumer reads the fact its own question needs.

    Two call sites where the union predicate was the wrong one, both found in
    review: ``dlsym`` resolvability is (c) alone, and an export-named surface
    metric counts (c), not public membership.
    """

    def test_dlsym_resolvable_names_need_a_confirmed_export(self) -> None:
        from abicheck.appcompat import _snapshot_export_names

        promised_unexported = _fn(
            name="inline_api",
            mangled="inline_api",
            is_extern_c=True,
            declared_in_headers_fact=_TRUE,
            in_public_contract_fact=_TRUE,
            binary_exported_fact=_FALSE,
        )
        exported = _fn(
            name="real_api",
            mangled="real_api",
            is_extern_c=True,
            declared_in_headers_fact=_TRUE,
            in_public_contract_fact=_TRUE,
            binary_exported_fact=_TRUE,
        )
        names = _snapshot_export_names(_snap("1.0", promised_unexported, exported))
        assert "real_api" in names
        assert "inline_api" not in names, (
            "dlsym cannot resolve a promised-but-unexported declaration, so it "
            "must not satisfy a required entrypoint"
        )

    def test_the_json_report_carries_the_block_for_a_real_comparison(self) -> None:
        """Through the real reporter, not just the `Change` field: the whole
        point of the block is that a *reader* can tell the three apart."""
        import json

        from abicheck.reporter import to_json

        declared = {
            "declared_in_headers_fact": _TRUE,
            "in_public_contract_fact": _TRUE,
        }
        old = _snap("1.0", _fn(**declared, binary_exported_fact=_TRUE))
        new = _snap("2.0", _fn(**declared, binary_exported_fact=_FALSE))
        payload = json.loads(to_json(compare(old, new)))
        blocks = [
            c["surface_facts"] for c in payload["changes"] if "surface_facts" in c
        ]
        assert blocks, payload["changes"]
        assert blocks[0] == {
            "declared_in_headers": "true",
            "in_public_contract": "true",
            "binary_exported": "false",
        }

    def test_the_report_schema_declares_the_surface_facts_block(self) -> None:
        """A schema-version bump nobody can discover from the schema is not a
        published field (Codex review, P2)."""
        import json
        from pathlib import Path

        schema = json.loads(
            (
                Path(__file__).resolve().parent.parent
                / "abicheck/schemas/compare_report.schema.json"
            ).read_text(encoding="utf-8")
        )
        block = schema["$defs"]["change"]["properties"]["surface_facts"]
        assert set(block["required"]) == set(surface_fact_summary(_fn()))
        for name in block["required"]:
            assert set(block["properties"][name]["enum"]) == {
                "true",
                "false",
                "unknown",
            }

    def test_export_named_metrics_count_the_export_fact(self) -> None:
        from abicheck.surface_graph import compute_surface_metrics

        snap = _snap(
            "1.0",
            _fn(
                name="exported",
                mangled="_Z8exportedv",
                declared_in_headers_fact=_TRUE,
                in_public_contract_fact=_TRUE,
                binary_exported_fact=_TRUE,
            ),
            _fn(
                name="inline_only",
                mangled="_Z11inline_onlyv",
                declared_in_headers_fact=_TRUE,
                in_public_contract_fact=_TRUE,
                binary_exported_fact=_FALSE,
            ),
        )
        metrics = compute_surface_metrics(snap)
        assert metrics.public_functions == 2
        assert metrics.exported_symbols == 1, (
            "an export-named counter must not count a promised, unexported "
            "declaration as a binary export"
        )


class TestPublicInlineWithoutExport:
    """(2) A public inline function with no export is not reported as
    removed -- on either side, and regardless of which side holds it."""

    @pytest.mark.parametrize("swap", [False, True])
    @pytest.mark.parametrize(
        "unexported",
        [_FALSE, Fact.not_collected("header-only dump"), Fact.partial(False)],
    )
    def test_an_unexported_declared_inline_is_not_a_removal(
        self, unexported: Fact[bool], swap: bool
    ) -> None:
        inline = _fn(
            name="Widget::size",
            mangled="_ZNK6Widget4sizeEv",
            is_inline=True,
            declared_in_headers_fact=_TRUE,
            in_public_contract_fact=_TRUE,
            binary_exported_fact=unexported,
        )
        exported_twin = _fn(
            name="Widget::size",
            mangled="_ZNK6Widget4sizeEv",
            is_inline=True,
            declared_in_headers_fact=_TRUE,
            in_public_contract_fact=_TRUE,
            binary_exported_fact=_TRUE,
        )
        first, second = (exported_twin, inline) if not swap else (inline, exported_twin)
        kinds = {
            c.kind for c in compare(_snap("1.0", first), _snap("2.0", second)).changes
        }
        assert ChangeKind.FUNC_REMOVED not in kinds
        assert ChangeKind.FUNC_REMOVED_ELF_ONLY not in kinds

    def test_it_stays_in_the_public_surface_when_a_header_set_declared_it(
        self, tmp_path: object
    ) -> None:
        from abicheck.provenance import apply_provenance

        header = "include/widget.h"
        fn = _fn(
            name="Widget::size",
            mangled="_ZNK6Widget4sizeEv",
            source_location=f"/src/{header}:12",
            declared_in_headers_fact=_TRUE,
            binary_exported_fact=_FALSE,
        )
        snap = _snap("1.0", fn)
        apply_provenance(snap, public_headers=[f"/src/{header}"])
        assert surface_fact_summary(fn)["in_public_contract"] == "true"
        assert surface_fact_summary(fn)["binary_exported"] == "false"


class TestAbsentHeadersProduceUnknown:
    """(3) Absent headers produce unknown, never a negative claim."""

    def test_export_only_entries_leave_the_declaration_unknown(self) -> None:
        from abicheck.extract.export_symbol_identity import (
            itanium_export_function,
            itanium_export_variable,
        )

        for decl in (
            itanium_export_function("_Z3foov"),
            itanium_export_variable("_Z3barv"),
        ):
            summary = surface_fact_summary(decl)
            assert summary["declared_in_headers"] == "unknown", summary
            assert summary["binary_exported"] == "true", summary

    def test_debug_info_is_not_header_evidence(self) -> None:
        from abicheck.extract.surface_fact_producers import debug_info_surface_facts

        for exported in (True, False):
            facts = debug_info_surface_facts(exported=exported)
            fn = _fn(**facts)  # type: ignore[arg-type]
            assert surface_fact_summary(fn)["declared_in_headers"] == "unknown"
            assert surface_fact_summary(fn)["binary_exported"] == str(exported).lower()

    def test_a_depth_projection_gives_header_evidence_back_as_unknown(self) -> None:
        """Discarding evidence must not turn into asserting its negation."""
        from abicheck.model.surface_facts import headers_discarded_surface_facts

        fn = _fn(
            declared_in_headers_fact=_TRUE,
            in_public_contract_fact=_TRUE,
            binary_exported_fact=_TRUE,
        )
        for name, fact in headers_discarded_surface_facts(reason="projected").items():
            setattr(fn, name, fact)
        summary = surface_fact_summary(fn)
        assert summary["declared_in_headers"] == "unknown"
        assert summary["in_public_contract"] == "unknown"
        assert summary["binary_exported"] == "true"

    @pytest.mark.parametrize("visibility", list(Visibility))
    def test_the_legacy_bridge_never_invents_header_evidence(
        self, visibility: Visibility
    ) -> None:
        """A pre-split snapshot carries only the conflated enum, and no
        member of it can establish fact (a) in either direction: not a
        positive out of "it was exported", and -- the half this test
        originally got wrong -- not a negative out of ``ELF_ONLY`` either.

        That branch used to assert ``"false"`` here on the reasoning that a
        header parse must have run and not accounted for the symbol. It need
        not have: both header-AST backends assign ``ELF_ONLY`` to a
        declaration they parsed *out of a header* whose symbol turned up in
        ``.symtab`` rather than the dynamic table, and a headerless snapshot
        reaches the same member with no header parse at all (Codex review,
        P2). See ``TestLegacyElfOnlyKeepsHeaderEvidenceUnknown`` for the
        counterexample and the exhaustive form of this invariant."""
        fn = _fn(visibility=visibility)
        assert surface_fact_summary(fn)["declared_in_headers"] == "unknown"


class TestAccessorContract:
    """The accessors' own contract, stated directly rather than only through
    a detector — the "primitive-level property tests" AGENTS.md asks for when
    a reusable predicate family is added."""

    @pytest.mark.parametrize(
        ("state", "expect_true", "expect_false"),
        [(_TRUE, True, False), (_FALSE, False, True), (_UNKNOWNS[1], False, False)],
    )
    def test_confirmed_predicates_partition_every_state(
        self, state: Fact[bool], expect_true: bool, expect_false: bool
    ) -> None:
        assert is_confirmed_true(state) is expect_true
        assert is_confirmed_false(state) is expect_false
        assert is_unknown(state) is (not expect_true and not expect_false)

    @pytest.mark.parametrize("exported", [_TRUE, _FALSE, _UNKNOWNS[1]])
    @pytest.mark.parametrize("contract", [_TRUE, _FALSE, _UNKNOWNS[1]])
    @pytest.mark.parametrize("declared", [_TRUE, _FALSE, _UNKNOWNS[1]])
    def test_in_public_surface_prefers_contract_then_export_then_declaration(
        self, declared: Fact[bool], contract: Fact[bool], exported: Fact[bool]
    ) -> None:
        from abicheck.model import in_public_surface

        fn = _fn(
            declared_in_headers_fact=declared,
            in_public_contract_fact=contract,
            binary_exported_fact=exported,
        )
        # The oracle is the documented precedence, spelled out independently
        # of the implementation's own branch order.
        if is_confirmed_true(contract):
            expected = True
        elif is_confirmed_false(contract):
            expected = False
        elif is_confirmed_true(exported):
            expected = True
        elif is_confirmed_false(exported):
            expected = False
        else:
            expected = is_confirmed_true(declared)
        assert in_public_surface(fn) is expected
        # is_abi_visible is the union with the export fact, always.
        from abicheck.model import is_abi_visible

        assert is_abi_visible(fn) is (is_confirmed_true(exported) or expected)

    def test_is_header_declared_and_its_negative_are_not_complements(self) -> None:
        """Unknown is neither: that is the whole point of keeping it."""
        from abicheck.model import declaration_confirmed_absent, is_header_declared

        unknown = _fn(
            visibility=Visibility.HIDDEN,
            declared_in_headers_fact=_UNKNOWNS[1],
        )
        assert not is_header_declared(unknown)
        assert not declaration_confirmed_absent(unknown)

    def test_binary_export_accessors_agree_with_the_stored_fact(self) -> None:
        from abicheck.model import is_binary_exported, is_export_confirmed_absent

        assert is_binary_exported(_fn(binary_exported_fact=_TRUE))
        assert is_export_confirmed_absent(_fn(binary_exported_fact=_FALSE))
        unknown = _fn(binary_exported_fact=_UNKNOWNS[1])
        assert not is_binary_exported(unknown)
        assert not is_export_confirmed_absent(unknown)

    def test_legacy_derivation_is_marked_as_such(self) -> None:
        from abicheck.model import is_legacy_derived

        bridged = _fn()
        for accessor in (declared_in_headers, in_public_contract, binary_exported):
            assert is_legacy_derived(accessor(bridged)), accessor.__name__
        stated = _fn(
            declared_in_headers_fact=_TRUE,
            in_public_contract_fact=_TRUE,
            binary_exported_fact=_TRUE,
        )
        for accessor in (declared_in_headers, in_public_contract, binary_exported):
            assert not is_legacy_derived(accessor(stated)), accessor.__name__

    def test_a_legacy_record_with_header_provenance_declares_from_it(self) -> None:
        """The bridge's one positive: a recorded source location is header
        provenance, even though the enum itself never said so."""
        with_location = _fn(source_location="include/widget.h:12")
        assert surface_fact_summary(with_location)["declared_in_headers"] == "true"
        assert surface_fact_summary(_fn())["declared_in_headers"] == "unknown"

    @pytest.mark.parametrize("origin", list(ScopeOrigin))
    def test_public_header_contract_fact_only_ever_adds_a_positive(
        self, origin: ScopeOrigin
    ) -> None:
        from abicheck.model.surface_facts import public_header_contract_fact

        fn = _fn(in_public_contract_fact=_UNKNOWNS[1])
        result = public_header_contract_fact(fn, origin)
        if origin is ScopeOrigin.PUBLIC_HEADER:
            assert result is not None and result.value is True
        else:
            assert result is None, "a non-public origin must not assert a negative"
        # Never overrides an already-confirmed positive, so the scope pass is
        # idempotent and cannot downgrade a producer's own answer.
        assert (
            public_header_contract_fact(
                _fn(in_public_contract_fact=_TRUE), ScopeOrigin.PUBLIC_HEADER
            )
            is None
        )

    @pytest.mark.parametrize(
        ("declared", "contract", "expected"),
        [
            # Each confirmed negative excludes on its own ...
            (_FALSE, _TRUE, False),
            (_TRUE, _FALSE, False),
            (_FALSE, _FALSE, False),
            # ... and past those, membership needs affirmative evidence from
            # one of the two facts. "Both unknown" is no evidence at all, not
            # a declaration (Codex review, P2: it admitted debug-info-only
            # records with neither header nor export evidence).
            (_UNKNOWNS[1], _UNKNOWNS[1], False),
            (_TRUE, _UNKNOWNS[1], True),
            (_UNKNOWNS[1], _TRUE, True),
        ],
    )
    def test_source_declaration_membership_needs_affirmative_evidence(
        self, declared: Fact[bool], contract: Fact[bool], expected: bool
    ) -> None:
        for exported in (_TRUE, _FALSE, _UNKNOWNS[1]):
            fn = _fn(
                declared_in_headers_fact=declared,
                in_public_contract_fact=contract,
                binary_exported_fact=exported,
            )
            # The export fact never participates in this population, so the
            # answer must not move with it -- that invariance is what keeps a
            # lost export from reading as a lost declaration.
            assert in_source_declaration_index(fn) is expected, exported

    def test_a_header_only_dump_leaves_the_export_fact_unknown(self) -> None:
        from abicheck.extract.surface_fact_producers import header_ast_surface_facts

        facts = header_ast_surface_facts(exported=None, producer="castxml")
        summary = surface_fact_summary(_fn(**facts))  # type: ignore[arg-type]
        assert summary == {
            "declared_in_headers": "true",
            "in_public_contract": "unknown",
            "binary_exported": "unknown",
        }


class TestLegacyBridgeIsBehaviourPreserving:
    """A pre-split snapshot must classify exactly as it did before, or the
    split silently changes every stored baseline's verdict."""

    @pytest.mark.parametrize("visibility", list(Visibility))
    def test_public_surface_matches_the_old_enum_test(
        self, visibility: Visibility
    ) -> None:
        from abicheck.model import in_public_surface, is_abi_visible

        fn = _fn(visibility=visibility)
        assert in_public_surface(fn) == (visibility is Visibility.PUBLIC)
        assert is_abi_visible(fn) == (
            visibility in (Visibility.PUBLIC, Visibility.ELF_ONLY)
        )

    @pytest.mark.parametrize("visibility", list(Visibility))
    def test_round_tripping_a_snapshot_preserves_the_bridged_reading(
        self, visibility: Visibility, tmp_path: object
    ) -> None:
        import pathlib

        from abicheck.serialization import load_snapshot, save_snapshot

        assert isinstance(tmp_path, pathlib.Path)
        fn = _fn(visibility=visibility)
        var = Variable(name="v", mangled="_Z1v", type="int", visibility=visibility)
        snap = _snap("1.0", fn)
        snap.variables = [var]
        path = tmp_path / "snap.json"
        save_snapshot(snap, str(path))
        loaded = load_snapshot(str(path))
        assert surface_fact_summary(loaded.functions[0]) == surface_fact_summary(fn)
        assert surface_fact_summary(loaded.variables[0]) == surface_fact_summary(var)


class TestExportGainedIsRecordedNotDropped:
    """The export axis is not a one-way axis.

    Registered because the first revision of this split implemented the
    transition detector for export *loss* only. Before the split, a
    promised-but-unexported declaration failed the old
    ``visibility in (PUBLIC, ELF_ONLY)`` filter outright, so the old side was
    absent from the public index, the pair never matched, and a newly
    exported declaration was reported as ``FUNC_ADDED``. Once the
    declaration keeps its place on both sides the pair *matches* -- and with
    no gain-side detector the run reported nothing at all for a version
    script that newly exports an existing declaration. Codex review, P2.

    An addition that vanishes is what "record before disposing" forbids; a
    silent diff is a worse outcome than the wrong finding this split set out
    to remove.
    """

    @staticmethod
    def _decl(owner: str, exported: Fact[bool] | None) -> Function | Variable:
        facts: dict[str, object] = {
            "declared_in_headers_fact": _TRUE,
            "in_public_contract_fact": _TRUE,
            "binary_exported_fact": exported,
        }
        if owner == "variable":
            return Variable(
                name="g_registry",
                mangled="_Z10g_registry",
                type="int",
                **facts,  # type: ignore[arg-type]
            )
        return _fn(**facts)

    @staticmethod
    def _pair(owner: str, old_decl: object, new_decl: object) -> tuple[object, object]:
        old_snap, new_snap = _snap("1.0"), _snap("2.0")
        for snap, decl in ((old_snap, old_decl), (new_snap, new_decl)):
            if owner == "variable":
                snap.variables = [decl]  # type: ignore[list-item]
            else:
                snap.functions = [decl]  # type: ignore[list-item]
        return old_snap, new_snap

    @pytest.mark.parametrize(
        ("owner", "kind"),
        [
            ("function", ChangeKind.FUNC_EXPORT_ADDED),
            ("variable", ChangeKind.VAR_EXPORT_ADDED),
        ],
    )
    def test_a_gained_export_on_an_existing_declaration_is_reported(
        self, owner: str, kind: ChangeKind
    ) -> None:
        old_snap, new_snap = self._pair(
            owner, self._decl(owner, _FALSE), self._decl(owner, _TRUE)
        )
        result = compare(old_snap, new_snap)  # type: ignore[arg-type]
        kinds = {c.kind for c in result.changes}
        assert kind in kinds, [c.kind for c in result.changes]
        # The whole point: the run is not silent.
        assert result.changes
        stamped = [c for c in result.changes if c.kind is kind]
        assert stamped[0].surface_facts == {
            "declared_in_headers": "true",
            "in_public_contract": "true",
            "binary_exported": "true",
        }

    @pytest.mark.parametrize(
        ("owner", "kind"),
        [
            ("function", ChangeKind.FUNC_EXPORT_ADDED),
            ("variable", ChangeKind.VAR_EXPORT_ADDED),
        ],
    )
    def test_a_gained_export_is_compatible_not_breaking(
        self, owner: str, kind: ChangeKind
    ) -> None:
        """Recording the change must not manufacture a break out of good
        news: gaining an export takes nothing away from any consumer."""
        from abicheck.checker_policy import ADDITION_KINDS, COMPATIBLE_KINDS

        assert kind in COMPATIBLE_KINDS
        assert kind in ADDITION_KINDS
        old_snap, new_snap = self._pair(
            owner, self._decl(owner, _FALSE), self._decl(owner, _TRUE)
        )
        result = compare(old_snap, new_snap)  # type: ignore[arg-type]
        assert result.verdict is not Verdict.BREAKING
        assert result.verdict is not Verdict.API_BREAK

    @pytest.mark.parametrize("unknown", [f for f in _UNKNOWNS if f is not None])
    @pytest.mark.parametrize("owner", ["function", "variable"])
    def test_unknown_old_side_evidence_is_never_an_observed_gain(
        self, owner: str, unknown: Fact[bool]
    ) -> None:
        """The exact mirror of the loss side's own rule: "unknown before,
        exported now" is a gap in this run's evidence, not an observed
        addition, across every real ``FactStatus`` that spells unknown."""
        old_snap, new_snap = self._pair(
            owner, self._decl(owner, unknown), self._decl(owner, _TRUE)
        )
        kinds = {c.kind for c in compare(old_snap, new_snap).changes}  # type: ignore[arg-type]
        assert not kinds & {
            ChangeKind.FUNC_EXPORT_ADDED,
            ChangeKind.VAR_EXPORT_ADDED,
        }

    @pytest.mark.parametrize("owner", ["function", "variable"])
    def test_the_two_directions_are_exclusive_and_exhaustive(self, owner: str) -> None:
        """Across the full confirmed×confirmed grid, exactly one of the two
        transition findings fires, and only for a real transition.

        The oracle is the pair of booleans this test itself constructed, not
        either detector's own predicate.
        """
        gain = {ChangeKind.FUNC_EXPORT_ADDED, ChangeKind.VAR_EXPORT_ADDED}
        loss = {ChangeKind.FUNC_VISIBILITY_CHANGED, ChangeKind.VAR_VISIBILITY_CHANGED}
        for old_exported, new_exported in itertools.product((True, False), repeat=2):
            old_snap, new_snap = self._pair(
                owner,
                self._decl(owner, Fact.present(old_exported)),
                self._decl(owner, Fact.present(new_exported)),
            )
            kinds = {c.kind for c in compare(old_snap, new_snap).changes}  # type: ignore[arg-type]
            expect_gain = (not old_exported) and new_exported
            expect_loss = old_exported and not new_exported
            assert bool(kinds & gain) is expect_gain, (old_exported, new_exported)
            assert bool(kinds & loss) is expect_loss, (old_exported, new_exported)


class TestLegacyElfOnlyKeepsHeaderEvidenceUnknown:
    """``Visibility.ELF_ONLY`` cannot establish header *absence*.

    Registered because the legacy bridge originally read ``ELF_ONLY`` as a
    confirmed "not declared in any header". Both header-AST backends assign
    ``ELF_ONLY`` to a declaration they parsed **out of a header** whose
    symbol turned up in ``.symtab`` rather than the dynamic table
    (``extract/headers/castxml/location.visibility`` and its clang sibling),
    so that reading states a negative about a declaration a header parse
    produced -- and a pre-v46 headerless snapshot reaches the same branch
    with no header evidence at all. Codex review, P2.

    The record's own header provenance is the discriminator that actually
    answers fact (a), and it is consulted for every enum member alike.
    """

    def test_a_header_parsed_elf_only_declaration_is_header_declared(self) -> None:
        """The counterexample that falsifies the enum-keyed reading: castxml
        and clang both produce this for a static-only symbol."""
        fn = _fn(visibility=Visibility.ELF_ONLY, source_header="lib.h")
        assert is_header_declared(fn)
        assert not declaration_confirmed_absent(fn)

    def test_a_synthesized_export_table_entry_stays_unknown(self) -> None:
        """No header provenance: unknown, never a confirmed negative -- the
        user's own hard requirement for this split."""
        fn = _fn(visibility=Visibility.ELF_ONLY)
        assert is_unknown(declared_in_headers(fn))
        assert not declaration_confirmed_absent(fn)
        assert surface_fact_summary(fn)["declared_in_headers"] == "unknown"

    @pytest.mark.parametrize("vis", list(Visibility))
    def test_no_legacy_member_ever_claims_header_absence(self, vis: Visibility) -> None:
        """Exhaustive over the enum's whole domain, both with and without
        header provenance: the bridge may answer true or unknown, never a
        confirmed false. A confirmed false is a claim no ``Visibility`` value
        carries the evidence to make."""
        for header in (None, "lib.h"):
            fn = _fn(visibility=vis, source_header=header)
            assert not is_confirmed_false(declared_in_headers(fn)), (vis, header)

    def test_the_producer_question_is_still_answerable(self) -> None:
        """Removing the enum reading from fact (a) must not lose the
        *different* question ``ELF_ONLY`` really does answer -- which is what
        keeps the ELF-only removal kind and the stub-record consumers
        working."""
        assert is_export_table_only_record(_fn(visibility=Visibility.ELF_ONLY))
        assert is_export_table_only_record(
            _fn(visibility=Visibility.ELF_ONLY, source_header="lib.h")
        )
        assert not is_export_table_only_record(_fn(visibility=Visibility.PUBLIC))


class TestExportNamedSubjectsNeedTheIntersection:
    """A detector whose subject is a *binary* symbol wants (b) AND (c).

    Registered because the split replaced several ``visibility is
    Visibility.PUBLIC`` tests with :func:`in_public_surface` -- the *union* of
    (b) and (c) -- which silently widened each subject to declarations the
    artifact never exported. Codex review, P2, against
    ``diff_templates``' instantiation-survival index, whose own docstring
    says a stale ``extern template`` declaration must not count as
    surviving: that declaration is precisely a promised-but-unexported
    entity, so the union admitted it and suppressed the finding.
    """

    def test_is_public_export_never_admits_an_unconfirmed_export(self) -> None:
        """Exhaustive over the whole (a)×(b)×(c) fact domain: the property
        every calling detector actually depends on is that a *confirmed*
        export is necessary, so no amount of contract or header evidence can
        substitute for it.

        Stated as a necessary condition on the fact this test itself
        constructed rather than as a full truth table, deliberately: the
        sufficient half is not a second copy of
        :func:`in_public_surface`'s documented precedence chain (contract,
        then export, then declaration), and restating that chain here would
        make this test a mirror of the implementation instead of a check on
        it. The chain's own behaviour is pinned by
        ``TestThreeFactsStayThreeFacts``; the legacy equivalence that makes
        this predicate a safe substitution is pinned below.
        """
        states = [f for f in _STATES if f is not None]
        for declared, contract, exported in itertools.product(states, repeat=3):
            fn = _fn(
                declared_in_headers_fact=declared,
                in_public_contract_fact=contract,
                binary_exported_fact=exported,
            )
            if _expected_tri(exported) != "true":
                assert not is_public_export(fn), (declared, contract, exported)
            # And it is never broader than either half it intersects.
            if is_public_export(fn):
                assert is_binary_exported(fn)
                assert in_public_surface(fn)
                assert is_abi_visible(fn)

    @pytest.mark.parametrize(
        ("vis", "expected"),
        [
            (Visibility.PUBLIC, True),
            (Visibility.ELF_ONLY, False),
            (Visibility.HIDDEN, False),
        ],
    )
    def test_it_reproduces_the_legacy_is_public_test_exactly(
        self, vis: Visibility, expected: bool
    ) -> None:
        """The equivalence that makes this safe to substitute for every
        ``visibility is Visibility.PUBLIC`` site: identical on every
        pre-split snapshot, differing only for the state the enum could not
        hold."""
        assert is_public_export(_fn(visibility=vis)) is expected

    def test_a_promised_unexported_declaration_is_the_one_difference(self) -> None:
        assert not is_public_export(
            _fn(
                declared_in_headers_fact=_TRUE,
                in_public_contract_fact=_TRUE,
                binary_exported_fact=_FALSE,
            )
        )

    def test_a_stale_extern_template_declaration_does_not_mask_the_removal(
        self,
    ) -> None:
        """The reported case, end to end through ``compare``: the header
        still declares the instantiation, the new binary no longer exports
        it, and the enclosing template survives -- so the finding this
        detector exists for must fire."""
        mangled_old = "_ZN10descriptorIfE9thresholdEv"
        surviving = "_ZN10descriptorIdE9thresholdEv"

        def inst(mangled: str, exported: Fact[bool]) -> Function:
            return _fn(
                name="threshold",
                mangled=mangled,
                declared_in_headers_fact=_TRUE,
                in_public_contract_fact=_TRUE,
                binary_exported_fact=exported,
            )

        old_snap = _snap("1.0")
        old_snap.functions = [inst(mangled_old, _TRUE), inst(surviving, _TRUE)]
        new_snap = _snap("2.0")
        # The stale `extern template` declaration: still in the headers,
        # no longer emitted into the binary.
        new_snap.functions = [inst(mangled_old, _FALSE), inst(surviving, _TRUE)]
        kinds = {c.kind for c in compare(old_snap, new_snap).changes}
        assert ChangeKind.INSTANTIATION_MISSING_FROM_BINARY in kinds, sorted(
            k.value for k in kinds
        )
