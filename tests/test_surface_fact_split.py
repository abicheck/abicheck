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

from abicheck.checker import compare
from abicheck.extract.surface_fact_producers import (
    debug_info_surface_facts,
    export_table_surface_facts,
    header_ast_surface_facts,
)
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

        dwarf_decl = _fn(**debug_info_surface_facts(exported=True))  # type: ignore[arg-type]
        assert surface_fact_summary(dwarf_decl)["declared_in_headers"] == "unknown"
        assert in_source_declaration_index(dwarf_decl)


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


class TestRemovingAnUnexportedDeclarationIsNotABinaryBreak:
    """The removal half of the same class as
    :class:`TestPublicInlineWithoutExport` above.

    That class covers a declaration that survives on both sides while only
    its export differs. This one covers the declaration that is genuinely
    *gone* from the new side while the old side confirmed it was never
    exported -- a header-only inline, a hidden friend, a declaration a
    version script already kept out of the dynamic table. It is a real
    removal, but not a *binary* one: with no symbol in the old artifact,
    nothing a consumer linked against can fail to bind to, which is the only
    thing ``FUNC_REMOVED``'s own impact asserts ("old binaries call a symbol
    that no longer exists").

    Reached because the export axis deliberately keeps such a declaration in
    the comparison population (``export_transition.
    has_observed_contract_evidence``) -- it must, or a regained export
    reports as ``FUNC_ADDED``. Keeping it is right; scoring its removal on
    the binary axis was not, and it did so for every such declaration, which
    is why this enumerates the confirmed-unexported fact domain and several
    independent declaration shapes rather than the one that surfaced it
    (catalog ``case96_hidden_friend_removed``, whose verdict went
    ``API_BREAK`` -> ``BREAKING``).

    The oracle is the ``ChangeKind`` registry's own verdict partition, read
    straight from ``checker_policy``. Deliberately not the branch in
    ``diff_symbols._check_removed_function`` that the fix edited: asking
    that function which kind it picks would restate the implementation.
    """

    #: Every way this run can *confirm* "not exported". An unknown export
    #: fact is excluded on purpose -- it is the case where the binary-axis
    #: reading is still the honest one.
    _CONFIRMED_UNEXPORTED = (
        Fact.present(False),
        Fact.partial(False),
    )

    #: Declaration shapes that reach the same path for independent reasons.
    _SHAPES: tuple[tuple[str, str, dict[str, object]], ...] = (
        ("hidden friend", "_ZN5mylibeqERKNS_5pointES2_", {"name": "operator=="}),
        (
            "header-only inline",
            "_ZNK6Widget4sizeEv",
            {"name": "Widget::size", "is_inline": True},
        ),
        ("plain free function", "_Z3foov", {"name": "foo"}),
        (
            "member function",
            "_ZN6Widget6resizeEi",
            {"name": "Widget::resize"},
        ),
    )

    @pytest.mark.parametrize("unexported", _CONFIRMED_UNEXPORTED)
    @pytest.mark.parametrize("shape,mangled,extra", _SHAPES)
    def test_no_removal_of_a_confirmed_unexported_declaration_is_breaking(
        self,
        shape: str,
        mangled: str,
        extra: dict[str, object],
        unexported: Fact[bool],
    ) -> None:
        from abicheck.checker_policy import BREAKING_KINDS

        old = _fn(
            mangled=mangled,
            declared_in_headers_fact=_TRUE,
            in_public_contract_fact=_TRUE,
            binary_exported_fact=unexported,
            **extra,
        )
        changes = compare(_snap("1.0", old), _snap("2.0")).changes
        offenders = [c.kind for c in changes if c.kind in BREAKING_KINDS]
        assert not offenders, (
            f"{shape}: removing a declaration this run confirmed was never "
            f"exported produced binary-break finding(s) {offenders}"
        )

    @pytest.mark.parametrize("shape,mangled,extra", _SHAPES)
    def test_the_removal_is_still_reported_as_an_api_break(
        self, shape: str, mangled: str, extra: dict[str, object]
    ) -> None:
        """The negative control for the assertion above: "no breaking
        finding" must not be reached by reporting nothing at all."""
        from abicheck.checker_policy import API_BREAK_KINDS

        old = _fn(
            mangled=mangled,
            declared_in_headers_fact=_TRUE,
            in_public_contract_fact=_TRUE,
            binary_exported_fact=_FALSE,
            **extra,
        )
        kinds = {c.kind for c in compare(_snap("1.0", old), _snap("2.0")).changes}
        assert kinds & API_BREAK_KINDS, f"{shape}: the removal vanished entirely"

    @pytest.mark.parametrize("shape,mangled,extra", _SHAPES)
    def test_a_confirmed_exported_declaration_still_breaks(
        self, shape: str, mangled: str, extra: dict[str, object]
    ) -> None:
        """The other direction, so the fix cannot pass by never reporting a
        binary break: the same removal with the export fact flipped to a
        confirmed `True` is still `FUNC_REMOVED`."""
        old = _fn(
            mangled=mangled,
            declared_in_headers_fact=_TRUE,
            in_public_contract_fact=_TRUE,
            binary_exported_fact=_TRUE,
            **extra,
        )
        kinds = {c.kind for c in compare(_snap("1.0", old), _snap("2.0")).changes}
        assert ChangeKind.FUNC_REMOVED in kinds, shape


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
        # Read `assert is_export_table_only_record` when this class was
        # written, on the assumption that the enum alone settled the producer.
        # It does not -- a record with header provenance came from a header
        # AST, so calling it a stub is the misclassification
        # `TestHeaderParsedStaticOnlyIsNotAStub` exists for (Codex review, P2).
        # This test pinned that bug until the fix landed.
        assert not is_export_table_only_record(
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


class TestHeaderParsedStaticOnlyIsNotAStub:
    """``ELF_ONLY`` is necessary but not sufficient for "export-table stub".

    Registered because splitting fact (a) out of the enum closed only one of
    the two places that read header absence out of ``ELF_ONLY``. This accessor
    still keyed on the enum alone, so a declaration a header-AST backend
    parsed whose symbol turned up in ``.symtab`` rather than the dynamic table
    — which both backends give ``ELF_ONLY`` — was classified as a stub:
    dropped from the source-declaration index, its fully parsed signature
    rejected by ``bundle_signature_evidence``, its removal mislabellable
    ``FUNC_REMOVED_ELF_ONLY``. Codex review, P2.

    Fact (a) is what separates the two producers, which is why it had to stop
    being derived from the enum before this could be fixed at all.
    """

    @staticmethod
    def _header_parsed_static_only() -> Function:
        """What the header-AST backends really build for this case: through
        the actual producer helper, not a hand-set fact triple."""
        return _fn(
            name="threshold",
            mangled="_ZN3lib9thresholdEv",
            visibility=Visibility.ELF_ONLY,
            source_header="lib.h",
            **header_ast_surface_facts(exported=False, producer="castxml"),
        )

    def test_a_header_parsed_static_only_declaration_is_not_a_stub(self) -> None:
        fn = self._header_parsed_static_only()
        assert is_header_declared(fn)  # the premise: the backend affirms (a)
        assert not is_export_table_only_record(fn)
        assert in_source_declaration_index(fn)  # the consequence

    def test_a_synthesized_export_entry_is_still_a_stub(self) -> None:
        """Negative control: the fix must not stop recognising a real stub,
        which ``FUNC_REMOVED_ELF_ONLY`` and the demangling paths rest on."""
        stub = _fn(
            name="_Z3foov",
            mangled="_Z3foov",
            visibility=Visibility.ELF_ONLY,
            **export_table_surface_facts(),
        )
        assert not is_header_declared(stub)
        assert is_export_table_only_record(stub)
        assert not in_source_declaration_index(stub)

    def test_it_is_exactly_elf_only_without_header_evidence(self) -> None:
        """Exhaustive over enum × every (a) state, against an oracle built
        from what this test set rather than the module's own predicate.

        Covers both halves of the rule. That the enum stays *necessary* is
        what keeps the DWARF case right — its (a) is unknown too, so a rule
        resting on (a) alone would misfile it, which is the regression
        ``catalog/cases/case97_api_depends_on_consumer_env`` caught when an
        earlier revision tried exactly that.
        """
        for vis in Visibility:
            for declared in (_TRUE, _FALSE, *_UNKNOWNS):
                fn = _fn(visibility=vis, declared_in_headers_fact=declared)
                expected = vis is Visibility.ELF_ONLY and (
                    _expected_tri(declared) != "true"
                )
                assert is_export_table_only_record(fn) is expected, (vis, declared)

    def test_a_legacy_elf_only_record_is_still_a_stub(self) -> None:
        """A pre-v46 snapshot carries no facts; the bridge leaves (a) unknown
        for a record with no header provenance, so the stored baselines that
        rely on the ELF-only removal kind are unchanged."""
        assert is_export_table_only_record(_fn(visibility=Visibility.ELF_ONLY))
