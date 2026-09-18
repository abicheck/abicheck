# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Every multi-library driver answers the product contract once.

`tests/test_release_public_surface.py` states the model itself against the
live directory `compare` fan-out. This module states the *uniformity*
claim: the three other drivers that compare several libraries at once --
a stored `BundleFacts` baseline against a live release, two stored
documents, and a multi-library ABICC descriptor -- reconcile the same one
product contract rather than repeating the Cartesian-product defect on
their own path.

The invariant each class states is deliberately driver-agnostic, since
that is exactly what a per-driver example test cannot state: given N
members whose exports partition one shared header surface, the driver
reports zero release-level missing exports; remove a declaration from
every member and it reports exactly one.
"""

from __future__ import annotations

import pytest

from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, Function, ScopeOrigin
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.workflows.release_public_surface import (
    member_pass_scope,
    reconcile_member_sets,
    stored_old_live_new_reconciliation,
)
from abicheck.workflows.release_surface_acquisition import (
    surface_from_bundle_facts,
    surface_from_snapshot,
    surface_from_snapshots,
    union_surfaces,
)

#: The shared umbrella header every member of these fixtures was compiled
#: against: three declarations, each exported by exactly one member.
_PRODUCT_API = ("api_a", "api_b", "api_c")


def _snapshot(library: str, *, declares: tuple[str, ...], exports: tuple[str, ...]):
    """A member that *declares* the product's whole header surface and
    *exports* only its own share -- the real shape of a multi-library
    product, and the one the Cartesian-product defect misread."""
    snap = AbiSnapshot(
        library=library,
        version="1.0",
        from_headers=True,
        elf=ElfMetadata(symbols=[ElfSymbol(name=n) for n in exports]),
    )
    snap.functions = [
        Function(
            name=name,
            mangled=name,
            return_type="void",
            origin=ScopeOrigin.PUBLIC_HEADER,
            # `extern "C"`: the obligation predicate is the single-artifact
            # check's own, and a bare-name exporter is the one shape whose
            # `mangled` may legitimately equal its display name.
            is_extern_c=True,
            source_header="/inc/product.h",
        )
        for name in declares
    ]
    return snap


def _members(*, declares: tuple[str, ...] = _PRODUCT_API, count: int = 3):
    """``{member: snapshot}`` partitioning *declares* across *count* members.

    Each member declares the whole surface (one umbrella header) and
    exports only the slice at its own index, so the union of exports
    satisfies the contract exactly and no single member does.
    """
    names = [f"lib{chr(ord('a') + i)}.so" for i in range(count)]
    # Round-robin, so the *union* of exports always covers the whole
    # product API however many members there are -- a fixture where the
    # union itself were short would make every "no missing export"
    # assertion below pass for the wrong reason.
    return {
        name: _snapshot(
            name,
            declares=declares,
            exports=tuple(
                symbol
                for position, symbol in enumerate(_PRODUCT_API)
                if position % count == index
            ),
        )
        for index, name in enumerate(names)
    }


def _missing_symbols(reconciliation) -> list[str]:
    return sorted(
        change.symbol
        for change in reconciliation.findings
        if change.kind == ChangeKind.PUBLIC_NOT_EXPORTED
    )


class _Facts:
    """The two attributes every driver reads off a stored bundle document.

    Duck-typed rather than a real ``BundleFacts``: these tests are about
    what the drivers *do* with a recorded contract, and constructing the
    full document would couple them to its schema without exercising one
    more line of the behaviour under test. ``storage``'s own round-trip
    tests own the document itself.
    """

    def __init__(self, snapshots, public_surface=None) -> None:
        self.per_library_snapshots = snapshots
        self.public_surface = public_surface


class TestTheUnionIsTakenOnEveryDriver:
    """N members, one shared header surface: zero missing exports."""

    def test_stored_pair(self) -> None:
        from abicheck.workflows.bundle_stored_pair_compare import (
            _stored_pair_public_surface,
        )

        members = _members()
        reconciliation = _stored_pair_public_surface(
            _Facts(members), _Facts(_members()), members
        )
        assert reconciliation is not None
        assert _missing_symbols(reconciliation) == []

    def test_stored_old_live_new(self) -> None:
        new = _members()
        surfaces = {
            name: surface_from_snapshot(snap, acquisition_key="k", side="new")
            for name, snap in new.items()
        }
        reconciliation = stored_old_live_new_reconciliation(
            _Facts(_members()), new, surfaces, unavailable={}
        )
        assert reconciliation is not None
        assert _missing_symbols(reconciliation) == []

    def test_compat_descriptor(self) -> None:
        from abicheck.compat.multi_library_run import _release_contract_findings

        findings = _release_contract_findings(_members(), _members())
        assert [
            f.symbol for f in findings if f.kind == ChangeKind.PUBLIC_NOT_EXPORTED
        ] == []


class TestARealRemovalIsStillReportedOnce:
    """The complement, and the reason the fold is not just suppression."""

    def _kept(self):
        """NEW keeps declaring the whole surface but nobody exports
        ``api_c`` any more -- a real product-level break."""
        return {
            name: _snapshot(name, declares=_PRODUCT_API, exports=exports)
            for name, exports in (("liba.so", ("api_a",)), ("libb.so", ("api_b",)))
        }

    def test_stored_pair(self) -> None:
        from abicheck.workflows.bundle_stored_pair_compare import (
            _stored_pair_public_surface,
        )

        new = self._kept()
        reconciliation = _stored_pair_public_surface(
            _Facts(_members(count=2)), _Facts(new), new
        )
        assert _missing_symbols(reconciliation) == ["api_c"]

    def test_stored_old_live_new(self) -> None:
        new = self._kept()
        surfaces = {
            name: surface_from_snapshot(snap, acquisition_key="k", side="new")
            for name, snap in new.items()
        }
        reconciliation = stored_old_live_new_reconciliation(
            _Facts(_members(count=2)), new, surfaces, unavailable={}
        )
        assert _missing_symbols(reconciliation) == ["api_c"]

    def test_compat_descriptor(self) -> None:
        from abicheck.compat.multi_library_run import _release_contract_findings

        findings = _release_contract_findings(_members(count=2), self._kept())
        assert sorted(
            f.symbol for f in findings if f.kind == ChangeKind.PUBLIC_NOT_EXPORTED
        ) == ["api_c"]


class TestNoDriverInventsAContract:
    """A side recording no public-header evidence records no contract."""

    def _binary_only(self, count: int = 2):
        return {
            f"lib{i}.so": AbiSnapshot(
                library=f"lib{i}.so",
                version="1.0",
                elf=ElfMetadata(symbols=[ElfSymbol(name=f"sym{i}")]),
            )
            for i in range(count)
        }

    def test_stored_pair_records_none(self) -> None:
        from abicheck.workflows.bundle_stored_pair_compare import (
            _stored_pair_public_surface,
        )

        members = self._binary_only()
        assert (
            _stored_pair_public_surface(_Facts(members), _Facts(members), members)
            is None
        )

    def test_compat_descriptor_records_none(self) -> None:
        from abicheck.compat.multi_library_run import _release_contract_findings

        assert (
            _release_contract_findings(self._binary_only(), self._binary_only()) == []
        )

    def test_an_unresolved_side_is_not_an_empty_promise(self) -> None:
        """The distinction the whole model rests on: `resolvable=False`
        carries a reason, and is not a surface with zero obligations."""
        surface = surface_from_snapshots(
            self._binary_only(), acquisition_key="k", side="old"
        )
        assert surface.resolvable is False
        assert surface.obligations == ()
        assert surface.unresolved_reason


class TestTheRecordedContractWins:
    """A stored document's own recorded surface, not a re-derivation."""

    def test_a_recorded_surface_is_preferred_over_the_members(self) -> None:
        """Reusing what the capture recorded is what keeps a stored
        comparison's answer identical to the live run that produced it."""
        recorded = surface_from_snapshots(
            _members(declares=("api_a", "api_b", "api_c", "api_recorded_only")),
            acquisition_key="recorded",
            side="new",
        )
        facts = _Facts(_members(), public_surface=recorded)
        assert "api_recorded_only" in {
            o.symbol for o in surface_from_bundle_facts(facts, side="new").obligations
        }

    def test_a_pre_v4_document_still_reconciles(self) -> None:
        """Every baseline captured before `public_surface` existed carries
        no recorded block; deriving one from its members is what keeps it
        comparable instead of silently losing its contract."""
        surface = surface_from_bundle_facts(_Facts(_members()), side="old")
        assert surface.resolvable
        assert {o.symbol for o in surface.obligations} == set(_PRODUCT_API)


class TestTheOwnershipScopeIsEnteredByEveryDriver:
    """The release level owns the check only above one member."""

    @pytest.mark.parametrize("count", [2, 3, 12])
    def test_several_members_move_the_check_to_the_release(self, count: int) -> None:
        from abicheck.workflows.crosscheck_ownership import (
            member_owned_checks,
            release_owned_checks,
        )

        names = [f"lib{i}.so" for i in range(count)]
        with member_pass_scope(names):
            assert "public_not_exported" in release_owned_checks()
            assert "public_not_exported" not in member_owned_checks(
                frozenset({"public_not_exported", "exported_not_public"})
            )

    @pytest.mark.parametrize("names", [[], ["only.so"], ["dup.so", "dup.so"]])
    def test_one_member_keeps_the_per_member_answer(self, names: list[str]) -> None:
        """Including a duplicate-named pair: one distinct member is one
        member, and the union of a single provider is that provider."""
        from abicheck.workflows.crosscheck_ownership import (
            member_owned_checks,
            release_owned_checks,
        )

        with member_pass_scope(names):
            assert release_owned_checks() == frozenset()
            assert "public_not_exported" in member_owned_checks(
                frozenset({"public_not_exported", "exported_not_public"})
            )

    def test_the_scope_is_restored_afterwards(self) -> None:
        from abicheck.workflows.crosscheck_ownership import release_owned_checks

        before = release_owned_checks()
        with member_pass_scope(["a.so", "b.so"]):
            pass
        assert release_owned_checks() == before


class TestTheUnionPrimitiveItself:
    """`union_surfaces`'s own contract, decoupled from any driver."""

    def _surfaces(self):
        return [
            surface_from_snapshot(snap, acquisition_key="k", side="new")
            for snap in _members().values()
        ]

    def test_the_union_does_not_depend_on_order(self) -> None:
        forward = union_surfaces(self._surfaces(), acquisition_key="k", side="new")
        reverse = union_surfaces(
            list(reversed(self._surfaces())), acquisition_key="k", side="new"
        )
        assert [o.symbol for o in forward.obligations] == [
            o.symbol for o in reverse.obligations
        ]
        assert forward.declared_symbols == reverse.declared_symbols

    def test_an_unresolvable_member_contributes_nothing(self) -> None:
        """It must not *narrow* the union either: a member with no header
        evidence proves nothing about what the product promises."""
        resolvable = self._surfaces()
        from abicheck.model.release_surface import unresolved_surface

        polluted = [
            *resolvable,
            unresolved_surface(acquisition_key="k", side="new", reason="none"),
        ]
        assert {
            o.symbol
            for o in union_surfaces(
                polluted, acquisition_key="k", side="new"
            ).obligations
        } == {
            o.symbol
            for o in union_surfaces(
                resolvable, acquisition_key="k", side="new"
            ).obligations
        }

    def test_all_unresolvable_is_unresolved_not_empty(self) -> None:
        from abicheck.model.release_surface import unresolved_surface

        result = union_surfaces(
            [unresolved_surface(acquisition_key="k", side="new", reason="none")],
            acquisition_key="k",
            side="new",
        )
        assert result.resolvable is False

    def test_the_union_of_one_is_that_one(self) -> None:
        one = self._surfaces()[:1]
        assert {
            o.symbol
            for o in union_surfaces(one, acquisition_key="k", side="new").obligations
        } == {o.symbol for o in one[0].obligations}


class TestABorrowedContractIsNeverAsserted:
    """Regression: a NEW side must not inherit OLD's promises.

    `stored_old_live_new_reconciliation` derived NEW's surface from the
    *stored OLD* snapshots while it was being written, which asserts that
    NEW still promises everything OLD did -- so every deliberately retired
    declaration read as a missing export. "Absent is not removed", at
    release level.
    """

    def test_a_declaration_retired_from_the_new_headers_is_not_a_missing_export(
        self,
    ) -> None:
        # NEW no longer declares `api_c` at all, and no longer exports it.
        new = {
            name: _snapshot(name, declares=("api_a", "api_b"), exports=exports)
            for name, exports in (("liba.so", ("api_a",)), ("libb.so", ("api_b",)))
        }
        surfaces = {
            name: surface_from_snapshot(snap, acquisition_key="k", side="new")
            for name, snap in new.items()
        }
        reconciliation = stored_old_live_new_reconciliation(
            _Facts(_members(count=2)), new, surfaces, unavailable={}
        )
        assert _missing_symbols(reconciliation) == []

    def test_a_new_side_with_no_header_evidence_records_no_contract(self) -> None:
        """A NEW side dumped at binary depth has no contract of its own,
        and must not be handed OLD's."""
        new = {
            name: AbiSnapshot(
                library=name,
                version="2.0",
                elf=ElfMetadata(symbols=[ElfSymbol(name="api_a")]),
            )
            for name in ("liba.so", "libb.so")
        }
        surfaces = {
            name: surface_from_snapshot(snap, acquisition_key="k", side="new")
            for name, snap in new.items()
        }
        reconciliation = stored_old_live_new_reconciliation(
            _Facts(_members(count=2)), new, surfaces, unavailable={}
        )
        assert reconciliation is not None
        assert reconciliation.new.surface_resolvable is False
        assert _missing_symbols(reconciliation) == []


class TestIncompleteCoverageNarrowsEveryDriver:
    """A member nobody read cannot make an absent symbol a removal."""

    def test_a_failed_new_member_suppresses_the_finding(self) -> None:
        new = {
            "liba.so": _snapshot("liba.so", declares=_PRODUCT_API, exports=("api_a",))
        }
        surfaces = {
            "liba.so": surface_from_snapshot(
                new["liba.so"], acquisition_key="k", side="new"
            )
        }
        reconciliation = stored_old_live_new_reconciliation(
            _Facts(_members(count=2)),
            new,
            surfaces,
            unavailable={"libb.so": "extraction failed"},
        )
        assert reconciliation is not None
        assert reconciliation.new.coverage_complete is False
        assert _missing_symbols(reconciliation) == []

    def test_the_same_run_with_every_member_read_does_report_it(self) -> None:
        """The vacuity guard on the assertion above: with no failure the
        identical inputs produce the finding, so the suppression is the
        coverage rule rather than a fixture that never had a finding."""
        new = {
            "liba.so": _snapshot("liba.so", declares=_PRODUCT_API, exports=("api_a",))
        }
        reconciliation = reconcile_member_sets(
            new_members=new,
            new_surface=surface_from_snapshots(new, acquisition_key="k", side="new"),
        )
        assert _missing_symbols(reconciliation) == ["api_b", "api_c"]


class TestTheStoredEnvelopeCarriesTheSection:
    """The stored-baseline document reports what it reconciled.

    A reconciliation the driver computes and no renderer emits is the
    "parser-only slice" that is not a shipped capability: the finding must
    reach the reader of every stored-baseline comparison's own document.
    """

    class _Result:
        def __init__(self, reconciliation) -> None:
            self.public_surface_reconciliation = reconciliation

    def _reconciliation(self, *, missing: bool):
        new = (
            {
                name: _snapshot(name, declares=_PRODUCT_API, exports=exports)
                for name, exports in (("liba.so", ("api_a",)), ("libb.so", ("api_b",)))
            }
            if missing
            else _members(count=2)
        )
        return reconcile_member_sets(
            new_members=new,
            new_surface=surface_from_snapshots(new, acquisition_key="k", side="new"),
        )

    def test_the_json_envelope_carries_the_block(self) -> None:
        from abicheck.frontends.cli.commands.compare_bundle_facts_sections import (
            public_surface_terms,
        )

        terms = public_surface_terms(self._Result(self._reconciliation(missing=True)))
        assert terms is not None
        doc = terms.to_dict()
        assert [f["symbol"] for f in doc["missing_exports"]] == ["api_c"]

    def test_a_comparison_with_no_contract_omits_it(self) -> None:
        """Absent rather than an empty section: an empty one reads as "the
        product promises nothing" instead of "no contract was recorded"."""
        from abicheck.frontends.cli.commands.compare_bundle_facts_sections import (
            public_surface_terms,
        )

        assert public_surface_terms(self._Result(None)) is None

    def test_the_markdown_reader_sees_it_too(self) -> None:
        from abicheck.frontends.cli.commands.compare_bundle_facts_sections import (
            public_surface_markdown_lines,
        )

        lines = public_surface_markdown_lines(
            self._Result(self._reconciliation(missing=True))
        )
        assert any("api_c" in line for line in lines)

    def test_a_clean_release_renders_no_missing_export(self) -> None:
        """The vacuity guard on the assertion above: the same renderer over
        a satisfied contract must not name a symbol, or "api_c appears"
        would hold for a renderer that printed every obligation."""
        from abicheck.frontends.cli.commands.compare_bundle_facts_sections import (
            public_surface_markdown_lines,
        )

        lines = public_surface_markdown_lines(
            self._Result(self._reconciliation(missing=False))
        )
        assert not any("api_c" in line for line in lines)


class TestTheCompatCommandReallyFoldsIt:
    """The ABICC descriptor path, through the real command.

    The helpers above are unit-tested directly; this asserts the command
    actually reaches them -- the gap a helper-only test cannot close, and
    the one that would let the whole fold sit unreachable behind a loop
    nobody wired.
    """

    def _run(self, tmp_path, monkeypatch, new_members):
        from click.testing import CliRunner

        from abicheck.checker import DiffResult, Verdict
        from abicheck.cli import main

        old_desc = tmp_path / "old.xml"
        new_desc = tmp_path / "new.xml"
        old_desc.write_text("<descriptor/>", encoding="utf-8")
        new_desc.write_text("<descriptor/>", encoding="utf-8")
        monkeypatch.setattr(
            "abicheck.compat.run_inputs._load_descriptor_or_dump",
            lambda *_a, **_k: AbiSnapshot(library="product", version="1.0"),
        )
        old_members = _members(count=2)
        names = sorted(new_members)
        monkeypatch.setattr(
            "abicheck.compat.cli._plan_library_pairs",
            lambda *_a, **_k: (
                [(tmp_path / name, tmp_path / name) for name in names],
                [],
                [],
            ),
        )

        def _pair(index: int):
            name = names[index]
            return old_members[sorted(old_members)[index]], new_members[name]

        # The command resolves member 0 through `_take_snapshots_with_logging`
        # (it keeps the per-phase log handlers) and each later member through
        # two `_snapshot_from_compat_input` calls, OLD then NEW -- so this
        # counter advances one member every second call.
        calls = {"one": 0}

        def _take(*_a, **_k):
            old, new = _pair(0)
            return old, "1.0", new, "2.0"

        def _one(*_a, **_k):
            member = 1 + calls["one"] // 2
            old, new = _pair(member)
            wanted = old if calls["one"] % 2 == 0 else new
            calls["one"] += 1
            return wanted, "2.0"

        monkeypatch.setattr("abicheck.compat.cli._take_snapshots_with_logging", _take)
        monkeypatch.setattr("abicheck.compat.cli._snapshot_from_compat_input", _one)
        monkeypatch.setattr(
            "abicheck.compat.cli.compare",
            lambda old, new, **_k: DiffResult(
                old_version="1.0",
                new_version="2.0",
                library=new.library,
                verdict=Verdict.NO_CHANGE,
                changes=[],
            ),
        )
        report = tmp_path / "report.html"
        runner = CliRunner()
        outcome = runner.invoke(
            main,
            [
                "compat",
                "check",
                "-lib",
                "product",
                "-old",
                str(old_desc),
                "-new",
                str(new_desc),
                # Into the test's own directory: the ABICC console summary
                # prints counts rather than symbols, so the assertion has to
                # read the report the user actually gets -- and the default
                # path would write into the repository working tree.
                "-report-path",
                str(report),
            ],
        )
        return outcome, report.read_text(encoding="utf-8") if report.exists() else ""

    def test_a_sibling_declaration_is_not_demanded_from_every_member(
        self, tmp_path, monkeypatch
    ) -> None:
        outcome, report = self._run(tmp_path, monkeypatch, _members(count=2))
        assert outcome.exit_code == 0, outcome.output
        assert "public_not_exported" not in report

    def test_a_declaration_nothing_exports_is_still_reported(
        self, tmp_path, monkeypatch
    ) -> None:
        """The vacuity guard: the same command over a product that really
        did drop `api_c` must still say so, or the assertion above would
        hold for a command that folded nothing because it found nothing."""
        dropped = {
            name: _snapshot(name, declares=_PRODUCT_API, exports=exports)
            for name, exports in (("liba.so", ("api_a",)), ("libb.so", ("api_b",)))
        }
        outcome, report = self._run(tmp_path, monkeypatch, dropped)
        assert "api_c" in report
        # Exactly once, not once per member -- the cardinality law itself.
        assert report.count("public_not_exported") == 1


class TestTheResolvedPolicyDocumentReachesReleaseScoring:
    """A `--policy` document moves a member's finding and the release-level
    finding that replaced it, or it has moved a check's meaning by moving
    its owner -- the one thing the ownership move may never do.
    """

    class _Stage:
        def __init__(self, findings) -> None:
            self.findings = tuple(findings)

    class _PolicyFile:
        def __init__(self, overrides) -> None:
            self.overrides = overrides

    def _stage(self):
        from abicheck.checker_types import Change

        return self._Stage(
            [
                Change(
                    kind=ChangeKind.PUBLIC_NOT_EXPORTED,
                    symbol="api_c",
                    description="missing",
                )
            ]
        )

    def test_an_override_moves_the_release_verdict(self) -> None:
        from abicheck.checker import Verdict
        from abicheck.workflows.release_public_surface import release_surface_verdict

        stage = self._stage()
        baseline = release_surface_verdict(stage)
        pinned = release_surface_verdict(
            stage,
            policy_file=self._PolicyFile(
                {ChangeKind.PUBLIC_NOT_EXPORTED: Verdict.BREAKING}
            ),
        )
        assert pinned == "BREAKING"
        # The vacuity guard: the override must have *changed* something, or
        # this passes against a function that ignores it entirely.
        assert baseline != "BREAKING"

    def test_the_override_can_lower_it_too(self) -> None:
        """Not only "pins to BREAKING": the document decides, in both
        directions, exactly as it does for a member."""
        from abicheck.checker import Verdict
        from abicheck.workflows.release_public_surface import release_surface_verdict

        assert (
            release_surface_verdict(
                self._stage(),
                policy_file=self._PolicyFile(
                    {ChangeKind.PUBLIC_NOT_EXPORTED: Verdict.COMPATIBLE}
                ),
            )
            == "COMPATIBLE"
        )

    def test_no_document_is_unchanged(self) -> None:
        from abicheck.workflows.release_public_surface import release_surface_verdict

        stage = self._stage()
        assert release_surface_verdict(stage) == release_surface_verdict(
            stage, policy_file=None
        )

    def test_the_severity_exit_takes_the_same_document(self) -> None:
        """`release_surface_severity_exit` already accepted `policy_file`;
        the defect was the call site never passing one. Stated here so the
        parameter cannot quietly stop being forwarded."""
        import inspect

        from abicheck import cli_compare_release
        from abicheck.workflows.release_public_surface import (
            release_surface_severity_exit,
        )

        assert (
            "policy_file" in inspect.signature(release_surface_severity_exit).parameters
        )
        source = inspect.getsource(cli_compare_release)
        # Both release-level scoring paths read one resolved object.
        assert source.count("_release_policy_file") >= 3


class TestTheOutputDirectorySummaryCarriesTheContract:
    """`--output-dir`'s `summary.json` is what a CI consumer collects when
    the directory is the artifact, so a block present only in the primary
    report is a block that consumer never sees -- the same drift the
    `comparison_scope`/`analysis_assurance` blocks are threaded through
    this writer to avoid.
    """

    def _terms(self, *, missing: bool):
        from abicheck.report.release_public_surface import (
            compute_release_public_surface,
        )

        new = (
            {
                name: _snapshot(name, declares=_PRODUCT_API, exports=exports)
                for name, exports in (("liba.so", ("api_a",)), ("libb.so", ("api_b",)))
            }
            if missing
            else _members(count=2)
        )
        from abicheck.workflows.release_surface_acquisition import (
            surface_from_snapshots,
        )

        reconciliation = reconcile_member_sets(
            new_members=new,
            new_surface=surface_from_snapshots(new, acquisition_key="k", side="new"),
        )
        return compute_release_public_surface(
            reconciliation, acquisition={}, shared_findings=()
        )

    def _summary(self, tmp_path, terms):
        import json

        from abicheck.frontends.cli.release_summary import _write_release_summary_file

        _write_release_summary_file(
            tmp_path,
            "NO_CHANGE",
            [],
            [],
            [],
            {},
            {},
            public_surface=terms,
        )
        return json.loads((tmp_path / "summary.json").read_text())

    def test_the_sidecar_states_the_missing_export(self, tmp_path) -> None:
        doc = self._summary(tmp_path, self._terms(missing=True))
        block = doc["public_surface_reconciliation"]
        assert [f["symbol"] for f in block["missing_exports"]] == ["api_c"]

    def test_a_satisfied_contract_names_no_missing_export(self, tmp_path) -> None:
        """The vacuity guard: the same writer over a satisfied contract must
        report none, or the assertion above holds for a writer that echoes
        every obligation."""
        doc = self._summary(tmp_path, self._terms(missing=False))
        assert doc["public_surface_reconciliation"]["missing_exports"] == []

    def test_a_release_with_no_contract_omits_the_block(self, tmp_path) -> None:
        assert "public_surface_reconciliation" not in self._summary(tmp_path, None)


class TestTheStoredEnvelopesGateBearingKeys:
    """`assurance_sections` moved into the sections module when the
    already-capped dispatch file could not absorb another section's
    assembly. It carries ADR-071 D9's *gate-bearing* scalar
    (`analysis_assurance_exit_contribution`), whose omission the module
    itself records as a real bypass rather than a cosmetic gap -- so it is
    stated here directly rather than left to whichever caller happens to
    exercise it.
    """

    def _decision(self, *, require_complete: bool, contribution: int = 1):
        from abicheck.policy.release_assurance import ReleaseAssuranceDecision

        return ReleaseAssuranceDecision(
            members=(),
            require_complete=require_complete,
            status="incomplete" if contribution else "complete",
            exit_contribution=contribution,
        )

    def test_the_gate_bearing_scalar_is_emitted_under_the_setting(self) -> None:
        from abicheck.frontends.cli.commands.compare_bundle_facts_sections import (
            assurance_sections,
        )

        block = assurance_sections(self._decision(require_complete=True))
        assert block["analysis_assurance_exit_contribution"] == 1
        assert "analysis_assurance" in block

    def test_a_clean_fold_still_states_its_zero(self) -> None:
        """Present-and-zero, not absent: a consumer reading the gate must be
        able to tell "the axis ran and found nothing" from "the axis did not
        run", which is the whole reason this key is a plain scalar."""
        from abicheck.frontends.cli.commands.compare_bundle_facts_sections import (
            assurance_sections,
        )

        block = assurance_sections(
            self._decision(require_complete=True, contribution=0)
        )
        assert block["analysis_assurance_exit_contribution"] == 0

    @pytest.mark.parametrize("decision", ["none", "not-required"])
    def test_without_the_setting_nothing_is_emitted(self, decision: str) -> None:
        """D4: present only under the setting, so every run that never asked
        for the axis is byte-identical to before it existed."""
        from abicheck.frontends.cli.commands.compare_bundle_facts_sections import (
            assurance_sections,
        )

        given = None if decision == "none" else self._decision(require_complete=False)
        assert assurance_sections(given) == {}

    def test_an_unevaluated_surface_renders_no_markdown_lines(self) -> None:
        """The empty-render branch: a reconciliation that states nothing
        contributes no lines rather than a bare heading."""
        from abicheck.frontends.cli.commands.compare_bundle_facts_sections import (
            public_surface_markdown_lines,
        )

        class _Result:
            public_surface_reconciliation = None

        assert public_surface_markdown_lines(_Result()) == []


class TestAReleaseFindingReachesTheVerdict:
    """A finding in the report that never reached the verdict is a gate
    bypass, not a cosmetic gap. The ABICC path merges its member verdicts
    *before* the release-level findings exist, so the fold has to re-score.
    """

    def _merged(self, verdict_name: str):
        from abicheck.checker import Verdict
        from abicheck.checker_types import DiffResult

        return DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="product",
            verdict=Verdict(verdict_name),
            changes=[],
        )

    def _members_holder(self, *, missing: bool):
        from abicheck.compat.multi_library_run import _MemberSnapshots

        holder = _MemberSnapshots()
        new = (
            {
                name: _snapshot(name, declares=_PRODUCT_API, exports=exports)
                for name, exports in (("liba.so", ("api_a",)), ("libb.so", ("api_b",)))
            }
            if missing
            else _members(count=2)
        )
        old = _members(count=2)
        for index, name in enumerate(sorted(new)):
            holder.record(index, None, None, old[sorted(old)[index]], new[name])
        # `record` keys off the member name it is given; re-key to the real
        # names so both sides line up the way the command's own loop does.
        holder.old = dict(zip(sorted(new), old.values(), strict=True))
        holder.new = new
        return holder

    def test_the_merged_verdict_is_raised_by_a_release_finding(self) -> None:
        result = self._members_holder(missing=True).fold_into(
            self._merged("NO_CHANGE"), policy="strict_abi"
        )
        assert [c.kind for c in result.changes] == [ChangeKind.PUBLIC_NOT_EXPORTED]
        assert result.verdict.value == "COMPATIBLE_WITH_RISK"

    def test_a_satisfied_contract_leaves_the_verdict_alone(self) -> None:
        """The vacuity guard: the fold must be driven by the finding, not
        applied unconditionally."""
        result = self._members_holder(missing=False).fold_into(
            self._merged("NO_CHANGE"), policy="strict_abi"
        )
        assert result.changes == []
        assert result.verdict.value == "NO_CHANGE"

    @pytest.mark.parametrize("worse", ["API_BREAK", "BREAKING"])
    def test_the_fold_never_lowers_a_members_verdict(self, worse: str) -> None:
        """Monotonic: a member's real break outranks a release-level risk
        finding, and the fold may never trade one for the other."""
        result = self._members_holder(missing=True).fold_into(
            self._merged(worse), policy="strict_abi"
        )
        assert result.verdict.value == worse


class TestTheWorstVerdictFoldIsOrdinal:
    """`_worst_verdict`'s own contract, decoupled from the descriptor path.

    A reusable max-by-rank primitive, so it gets the property treatment the
    repo asks for rather than only its caller's example.
    """

    _ORDER = (
        "NO_CHANGE",
        "COMPATIBLE",
        "COMPATIBLE_WITH_RISK",
        "API_BREAK",
        "BREAKING",
    )

    def _fold(self, current: str, release: str):
        from abicheck.checker import Verdict
        from abicheck.compat.multi_library_run import _worst_verdict

        return _worst_verdict(Verdict(current), release).value

    def test_every_pair_yields_the_worse_of_the_two(self) -> None:
        """Exhaustive over the whole 5x5 domain, against an oracle derived
        from the ordinal itself rather than from the function's own body."""
        disagreements = [
            (a, b, self._fold(a, b), expected)
            for a in self._ORDER
            for b in self._ORDER
            if (expected := max(a, b, key=self._ORDER.index)) != self._fold(a, b)
        ]
        assert disagreements == []

    def test_it_is_never_lowered(self) -> None:
        for a in self._ORDER:
            for b in self._ORDER:
                assert self._ORDER.index(self._fold(a, b)) >= self._ORDER.index(a)

    def test_an_unknown_release_spelling_never_wins(self) -> None:
        """Fail-safe rather than fail-open: a spelling this scale does not
        know must not outrank a real verdict by accident."""
        for a in self._ORDER:
            assert self._fold(a, "NOT_A_VERDICT") == a


class TestStoredInventoriesAreNotNarrowed:
    """Each stored document is a complete inventory of its own side, so its
    contract is reconciled against all of it. Pairing a full recorded
    contract with a key-filtered provider set makes an obligation only an
    unmatched member provides read as a missing export.
    """

    def test_an_unmatched_provider_still_satisfies_the_contract(self) -> None:
        from abicheck.workflows.bundle_stored_pair_compare import (
            _stored_pair_public_surface,
        )

        both = _members(count=3)
        # Only two of the three members matched between the documents --
        # the third still exports what it always did.
        matched = sorted(both)[:2]
        reconciliation = _stored_pair_public_surface(
            _Facts(both), _Facts(both), matched
        )
        assert reconciliation is not None
        assert _missing_symbols(reconciliation) == []

    def test_a_genuinely_absent_obligation_is_still_reported(self) -> None:
        """The vacuity guard: widening the inventory must not silence a real
        break, only stop inventing one."""
        from abicheck.workflows.bundle_stored_pair_compare import (
            _stored_pair_public_surface,
        )

        dropped = {
            name: _snapshot(name, declares=_PRODUCT_API, exports=exports)
            for name, exports in (("liba.so", ("api_a",)), ("libb.so", ("api_b",)))
        }
        reconciliation = _stored_pair_public_surface(
            _Facts(_members(count=2)), _Facts(dropped), sorted(dropped)
        )
        assert _missing_symbols(reconciliation) == ["api_c"]


class TestEveryUnavailableMemberNarrowsCoverage:
    """`failed` and `unsupported` fail loudly; `degraded` and
    `not_comparable` simply never reach the evidence. All four mean the same
    thing to the export index -- this member was not read -- so all four
    must reach it, or an obligation only that member provides is reported
    missing on evidence nobody gathered.
    """

    def _run(self, unavailable):
        new = {
            "liba.so": _snapshot("liba.so", declares=_PRODUCT_API, exports=("api_a",))
        }
        surfaces = {
            "liba.so": surface_from_snapshot(
                new["liba.so"], acquisition_key="k", side="new"
            )
        }
        return stored_old_live_new_reconciliation(
            _Facts(_members(count=2)), new, surfaces, unavailable=unavailable
        )

    @pytest.mark.parametrize(
        "reason",
        [
            "extraction failed",
            "unsupported artifact",
            "OLD captured degraded",
            "extraction contracts disagree",
        ],
    )
    def test_any_unread_reason_narrows_the_conclusion(self, reason: str) -> None:
        reconciliation = self._run({"libb.so": reason})
        assert reconciliation is not None
        assert reconciliation.new.coverage_complete is False
        assert _missing_symbols(reconciliation) == []

    def test_with_every_member_read_the_finding_is_reported(self) -> None:
        """The vacuity guard on all four: the identical inputs with nothing
        unavailable do produce the finding."""
        reconciliation = self._run({})
        assert reconciliation is not None
        assert reconciliation.new.coverage_complete is True
        assert _missing_symbols(reconciliation) == ["api_b", "api_c"]


class TestAnUnresolvedContractIsStatedNotOmitted:
    """`evaluated` goes false exactly when neither side's surface resolved.
    That is a stated fact carrying its own reason, not an absence -- the
    same "a failed extractor read as silence" inversion the Markdown
    renderer already refuses. Omitting it left a `--output-dir` consumer
    unable to tell an unresolved contract from no contract at all.
    """

    def _unresolved_terms(self):
        from abicheck.report.release_public_surface import (
            compute_release_public_surface,
        )

        binary_only = {
            name: AbiSnapshot(
                library=name,
                version="1.0",
                elf=ElfMetadata(symbols=[ElfSymbol(name="sym")]),
            )
            for name in ("liba.so", "libb.so")
        }
        reconciliation = reconcile_member_sets(
            new_members=binary_only,
            new_surface=surface_from_snapshots(
                binary_only, acquisition_key="k", side="new"
            ),
        )
        return compute_release_public_surface(
            reconciliation, acquisition={}, shared_findings=()
        )

    def _summary(self, tmp_path, terms):
        import json

        from abicheck.frontends.cli.release_summary import _write_release_summary_file

        _write_release_summary_file(
            tmp_path, "NO_CHANGE", [], [], [], {}, {}, public_surface=terms
        )
        return json.loads((tmp_path / "summary.json").read_text())

    def test_an_unresolved_surface_is_still_emitted(self, tmp_path) -> None:
        terms = self._unresolved_terms()
        assert terms.evaluated is False
        block = self._summary(tmp_path, terms)["public_surface_reconciliation"]
        assert block["sides"]["new"]["surface_resolvable"] is False
        assert block["sides"]["new"]["coverage_reason"]

    def test_no_reconciliation_at_all_still_omits_it(self, tmp_path) -> None:
        """The distinction the previous test rests on: "unresolved" and
        "absent" must stay two different documents."""
        assert "public_surface_reconciliation" not in self._summary(tmp_path, None)
