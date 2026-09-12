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
from abicheck.model import (
    AbiSnapshot,
    Fact,
    FactStatus,
    Function,
    Variable,
    Visibility,
    binary_exported,
    declared_in_headers,
    in_public_contract,
    in_source_declaration_index,
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
        """A pre-split snapshot carries only the conflated enum. The bridge
        may read a *negative* out of ``ELF_ONLY`` (a header parse did run
        and did not account for the symbol) but must never read a positive
        declaration out of "it was exported"."""
        fn = _fn(visibility=visibility)
        summary = surface_fact_summary(fn)
        if visibility is Visibility.ELF_ONLY:
            assert summary["declared_in_headers"] == "false"
        else:
            assert summary["declared_in_headers"] == "unknown"


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
