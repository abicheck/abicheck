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

"""The release-level product model: one public contract, many providers.

Covers the correction end to end at unit speed -- acquisition identity and
reuse, the bundle export index, the contract reconciliation and its coverage
honesty, the report section, and the shared-finding fold. The real-toolchain
end-to-end reproduction (the two-library ``product.h`` fixture, its three
extensions, and the header-extraction count on real ``castxml`` runs) lives
in ``tests/test_release_public_surface_integration.py``.

Bug class: ``tests/regressions/manifest.py``'s
``release_cartesian_product_contract``. The invariants here are stated over
*generated* member sets rather than one reported shape, because the defect
was a cardinality law, not a single bad finding: N members against one
shared surface produced N x |surface| findings, so the regression guard has
to be "the finding count does not grow with unrelated members", checked
across several N, not "libA no longer reports api_b".
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from abicheck.compare.bundle_export_index import (
    build_bundle_export_index,
    member_export_names,
)
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.release_surface import (
    PublicObligation,
    ReleasePublicSurface,
    SurfaceAcquisitionIdentity,
    unresolved_surface,
)
from abicheck.policy.evidence_status import CrossSourceEvolution
from abicheck.policy.release_contract_reconciliation import (
    reconcile_release,
    reconcile_side,
    release_owned_checks,
    undocumented_exports_by_member,
)
from abicheck.report.release_public_surface import (
    compute_release_public_surface,
    dedupe_shared_member_findings,
    render_release_public_surface_markdown,
)
from abicheck.workflows.crosscheck_ownership import (
    member_owned_checks,
    release_owned_checks_scope,
)
from abicheck.workflows.release_surface_acquisition import SurfaceAcquisitionLedger


@dataclass
class _Member:
    """A bundle member's export evidence, in the compact shape the release
    fan-out really keeps (``BundleSignatureEvidence``-shaped: an ``elf``
    attribute and nothing else the index needs)."""

    elf: ElfMetadata | None


def _member(*exports: str, default: bool = True) -> _Member:
    return _Member(
        elf=ElfMetadata(
            soname="",
            needed=[],
            symbols=[ElfSymbol(name=n, is_default=default) for n in exports],
            imports=[],
        )
    )


def _surface(
    *symbols: str,
    side: str = "new",
    key: str = "k",
    declared: tuple[str, ...] | None = None,
) -> ReleasePublicSurface:
    return ReleasePublicSurface(
        acquisition_key=key,
        side=side,
        obligations=tuple(
            PublicObligation(symbol=s, name=s, entity="function") for s in symbols
        ),
        declared_symbols=frozenset(declared if declared is not None else symbols),
    )


class TestSharedUmbrellaHeader:
    """Two libraries satisfying one shared umbrella header (scenarios 1/2)."""

    def test_neither_member_is_short_of_the_others_declaration(self) -> None:
        surface = _surface("api_a", "api_b")
        index = build_bundle_export_index(
            "new", {"libA.so": _member("api_a"), "libB.so": _member("api_b")}
        )
        side = reconcile_side(surface, index)
        assert side.obligations_total == 2
        assert side.satisfied_total == 2
        assert side.missing == ()
        assert side.unresolved == ()

    def test_a_sibling_provider_is_named_not_invented(self) -> None:
        index = build_bundle_export_index(
            "new", {"libA.so": _member("api_a"), "libB.so": _member("api_b")}
        )
        side = reconcile_side(_surface("api_a", "api_b"), index)
        assert side.satisfied["api_b"] == ("libB.so",)
        assert side.satisfied["api_a"] == ("libA.so",)


class TestAbsentFromTheWholeBundle:
    """A declaration no member provides (scenario 3)."""

    def test_one_finding_for_the_release_not_one_per_member(self) -> None:
        index = build_bundle_export_index(
            "new",
            {
                "libA.so": _member("api_a"),
                "libB.so": _member("api_b"),
                "libC.so": _member("api_c"),
            },
        )
        result = reconcile_release(
            _surface("api_a", "api_b", "gone"),
            index,
            old_surface=_surface("api_a", "api_b", "gone", side="old"),
            old_index=build_bundle_export_index(
                "old",
                {
                    "libA.so": _member("api_a"),
                    "libB.so": _member("api_b", "gone"),
                    "libC.so": _member("api_c"),
                },
            ),
        )
        assert [c.symbol for c in result.findings] == ["gone"]
        assert result.findings[0].kind is ChangeKind.PUBLIC_NOT_EXPORTED
        assert (
            result.findings[0].cross_source_evolution is CrossSourceEvolution.INTRODUCED
        )

    def test_the_finding_attributes_no_owning_library(self) -> None:
        """A symbol absent everywhere has no provider; naming one is the bug."""
        index = build_bundle_export_index("new", {"libA.so": _member("api_a")})
        result = reconcile_release(_surface("api_a", "gone"), index)
        finding = result.findings[0]
        assert "libA.so" not in finding.description
        assert finding.old_value == "gone"

    def test_a_gap_on_both_sides_is_persistent_not_introduced(self) -> None:
        both = {"libA.so": _member("api_a")}
        result = reconcile_release(
            _surface("api_a", "gone"),
            build_bundle_export_index("new", both),
            old_surface=_surface("api_a", "gone", side="old"),
            old_index=build_bundle_export_index("old", both),
        )
        assert (
            result.findings[0].cross_source_evolution is CrossSourceEvolution.PERSISTENT
        )

    def test_a_gap_only_on_old_is_resolved(self) -> None:
        result = reconcile_release(
            _surface("api_a", "back"),
            build_bundle_export_index("new", {"libA.so": _member("api_a", "back")}),
            old_surface=_surface("api_a", "back", side="old"),
            old_index=build_bundle_export_index("old", {"libA.so": _member("api_a")}),
        )
        assert [c.symbol for c in result.findings] == ["back"]
        assert (
            result.findings[0].cross_source_evolution is CrossSourceEvolution.RESOLVED
        )


class TestExportOwnership:
    """The same symbol exported by several members, and ownership moving
    between the old and new side (scenarios 5/9)."""

    def test_every_provider_of_a_shared_symbol_is_recorded(self) -> None:
        index = build_bundle_export_index(
            "new",
            {
                "libmkl_rt.so": _member("api_a"),
                "libmkl_core.so": _member("api_a"),
            },
        )
        assert index.providers("api_a") == ("libmkl_core.so", "libmkl_rt.so")
        assert reconcile_side(_surface("api_a"), index).satisfied["api_a"] == (
            "libmkl_core.so",
            "libmkl_rt.so",
        )

    def test_ownership_moving_between_members_is_not_a_finding(self) -> None:
        """The declaration is still provided by the product, by someone else."""
        old = build_bundle_export_index(
            "old", {"libA.so": _member("api_x"), "libB.so": _member()}
        )
        new = build_bundle_export_index(
            "new", {"libA.so": _member(), "libB.so": _member("api_x")}
        )
        result = reconcile_release(
            _surface("api_x"),
            new,
            old_surface=_surface("api_x", side="old"),
            old_index=old,
        )
        assert result.findings == ()
        assert result.new.satisfied["api_x"] == ("libB.so",)
        assert result.old is not None
        assert result.old.satisfied["api_x"] == ("libA.so",)


class TestVersionedExportAliases:
    """Versioned/default export spellings (scenario 6)."""

    def test_a_non_default_version_alias_does_not_satisfy_an_obligation(self) -> None:
        """The same rule the single-artifact check applies: an unversioned
        consumer link needs a default export, so a non-default alias is not
        a provider here either."""
        index = build_bundle_export_index(
            "new", {"libA.so": _member("api_a", default=False)}
        )
        assert index.providers("api_a") == ()
        assert [o.symbol for o in reconcile_side(_surface("api_a"), index).missing] == [
            "api_a"
        ]

    def test_a_default_export_does_satisfy_it(self) -> None:
        index = build_bundle_export_index(
            "new", {"libA.so": _member("api_a", default=True)}
        )
        assert index.providers("api_a") == ("libA.so",)

    def test_the_projection_is_the_shared_one(self) -> None:
        """Not a second notion of "exported": the index and the
        single-artifact check must read the same names off one member."""
        from abicheck.model.export_index import (
            build_raw_export_index_from_elf,
            default_versioned_names,
        )

        member = _member("api_a", "api_b")
        assert member.elf is not None
        assert member_export_names(member) == default_versioned_names(
            build_raw_export_index_from_elf(member.elf)
        )


class TestIncompleteCoverageIsHonest:
    """A failed member makes the reconciliation incomplete, never a false
    missing export (scenario 10)."""

    def test_a_failed_member_yields_unresolved_not_missing(self) -> None:
        index = build_bundle_export_index(
            "new",
            {"libA.so": _member("api_a")},
            failed_members={"libB.so": "dump failed"},
        )
        side = reconcile_side(_surface("api_a", "api_b"), index)
        assert side.coverage_complete is False
        assert side.missing == ()
        assert [o.symbol for o in side.unresolved] == ["api_b"]

    def test_no_high_confidence_finding_rests_on_an_unread_member(self) -> None:
        result = reconcile_release(
            _surface("api_a", "api_b"),
            build_bundle_export_index(
                "new",
                {"libA.so": _member("api_a")},
                failed_members={"libB.so": "dump failed"},
            ),
            old_surface=_surface("api_a", "api_b", side="old"),
            old_index=build_bundle_export_index(
                "old", {"libA.so": _member("api_a"), "libB.so": _member("api_b")}
            ),
        )
        assert result.findings == ()
        assert any("incomplete" in w for w in result.coverage_warnings)

    def test_the_successful_evidence_is_retained(self) -> None:
        side = reconcile_side(
            _surface("api_a", "api_b"),
            build_bundle_export_index(
                "new",
                {"libA.so": _member("api_a")},
                failed_members={"libB.so": "dump failed"},
            ),
        )
        assert side.satisfied == {"api_a": ("libA.so",)}

    def test_a_member_with_no_export_table_also_narrows_coverage(self) -> None:
        index = build_bundle_export_index(
            "new", {"libA.so": _member("api_a"), "libB.so": _Member(elf=None)}
        )
        assert index.members_without_exports == ("libB.so",)
        assert index.complete is False
        assert index.incompleteness_reason() is not None

    def test_an_unresolved_surface_is_not_an_empty_one(self) -> None:
        side = reconcile_side(
            unresolved_surface(acquisition_key="k", side="new", reason="no headers"),
            build_bundle_export_index("new", {"libA.so": _member("api_a")}),
        )
        assert side.surface_resolvable is False
        assert side.obligations_total == 0
        assert side.missing == ()


class TestAcquisitionIdentity:
    """Every AST-affecting input keys the acquisition (scenarios 11/12)."""

    #: One field per AST-affecting input. A value that does *not* change the
    #: key is an input that cannot influence reuse -- which is how two
    #: genuinely different requests would silently share one surface.
    DISTINGUISHING = (
        ("header_files", ("/a.h",)),
        ("header_dirs", ("/inc",)),
        ("exclude_headers", ("internal/*",)),
        ("includes", ("/dep/include",)),
        ("public_header_dirs", ("/inc/public",)),
        ("lang", "c++"),
        ("lang_explicit", True),
        ("backend", "clang"),
        ("frontend_context", "device"),
        ("compile_options", (("sysroot", "/sysroot"),)),
        ("depth", "build"),
        ("include_dependencies", True),
        ("build_config_digest", "abc123"),
    )

    @pytest.mark.parametrize("field,value", DISTINGUISHING)
    def test_each_input_changes_the_key(self, field: str, value: object) -> None:
        base = SurfaceAcquisitionIdentity()
        assert replace(base, **{field: value}).key() != base.key()

    def test_the_oracle_is_not_vacuous(self) -> None:
        """Guards the parametrized sweep above: an identity whose key ignored
        its inputs would pass every case by returning a constant, so assert
        the *unchanged* request keys equal."""
        base = SurfaceAcquisitionIdentity()
        assert base.key() == SurfaceAcquisitionIdentity().key()
        assert len({name for name, _ in self.DISTINGUISHING}) == len(
            self.DISTINGUISHING
        )

    def test_an_exclusion_rule_is_part_of_the_identity(self) -> None:
        """Scenario 11, stated on its own: two sides narrowed differently are
        not the same surface, so they must not share an acquisition."""
        plain = SurfaceAcquisitionIdentity(header_dirs=("/inc",))
        narrowed = replace(plain, exclude_headers=("internal/*",))
        assert plain.key() != narrowed.key()

    def test_the_key_is_stable_across_equal_requests(self) -> None:
        one = SurfaceAcquisitionIdentity(
            header_dirs=("/inc",), includes=("/dep",), lang="c"
        )
        assert one.key() == replace(one).key()


class TestAcquisitionLedger:
    """One acquisition per unique request, counted (scenario 12)."""

    def test_an_identical_request_is_acquired_once(self) -> None:
        ledger = SurfaceAcquisitionLedger()
        identity = SurfaceAcquisitionIdentity(header_dirs=("/inc",))
        calls: list[int] = []

        def _produce() -> ReleasePublicSurface:
            calls.append(1)
            return _surface("api_a")

        for side in ("old", "new", "new", "old"):
            ledger.acquire(identity, side, _produce)
        assert len(calls) == 1
        assert ledger.total_acquisitions == 1
        assert ledger.total_reuses == 3

    def test_a_different_request_acquires_its_own(self) -> None:
        ledger = SurfaceAcquisitionLedger()
        a = SurfaceAcquisitionIdentity(header_dirs=("/old/inc",))
        b = SurfaceAcquisitionIdentity(header_dirs=("/new/inc",))
        ledger.acquire(a, "old", lambda: _surface("x", side="old"))
        ledger.acquire(b, "new", lambda: _surface("x"))
        assert ledger.total_acquisitions == 2
        assert ledger.to_dict()["acquisitions"] == 2
        assert len(ledger.acquisitions_by_key()) == 2

    def test_concurrent_members_still_acquire_once(self) -> None:
        """Scenario 13's mechanism: the fan-out compares members in threads,
        so two members reaching one key together must not both acquire."""
        from concurrent.futures import ThreadPoolExecutor

        ledger = SurfaceAcquisitionLedger()
        identity = SurfaceAcquisitionIdentity(header_dirs=("/inc",))
        calls: list[int] = []

        def _produce() -> ReleasePublicSurface:
            calls.append(1)
            return _surface("api_a")

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(
                pool.map(lambda _: ledger.acquire(identity, "new", _produce), range(32))
            )
        assert len(calls) == 1

    def test_a_reused_surface_is_relabelled_for_the_asking_side(self) -> None:
        ledger = SurfaceAcquisitionLedger()
        identity = SurfaceAcquisitionIdentity(header_dirs=("/inc",))
        first = ledger.acquire(identity, "old", lambda: _surface("x", side="old"))
        reused = ledger.acquire(identity, "new", lambda: _surface("x")).for_side("new")
        assert first.side == "old"
        assert reused.side == "new"
        assert reused.acquisition_key == first.acquisition_key
        assert reused.obligations == first.obligations


class TestUndocumentedExportAccounting:
    """Undocumented exports, attributed to the exporting member (scenario 4)."""

    def test_the_count_lands_on_the_exporting_member(self) -> None:
        members = {
            "libA.so": _member("api_a"),
            "libB.so": _member("api_b", "internal_c"),
        }
        index = build_bundle_export_index("new", members)
        counts = undocumented_exports_by_member(
            _surface("api_a", "api_b"),
            index,
            {
                name: member_export_names(m) or frozenset()
                for name, m in members.items()
            },
        )
        assert counts == {"libA.so": 0, "libB.so": 1}

    def test_an_unresolvable_surface_accounts_nothing(self) -> None:
        """Not "every export is undocumented": with no declaration index
        there is nothing to judge an export against."""
        members = {"libA.so": _member("api_a")}
        counts = undocumented_exports_by_member(
            unresolved_surface(acquisition_key="k", side="new", reason="no headers"),
            build_bundle_export_index("new", members),
            {"libA.so": frozenset({"api_a"})},
        )
        assert counts == {}

    def test_the_release_totals_split_documented_from_undocumented(self) -> None:
        index = build_bundle_export_index(
            "new", {"libA.so": _member("api_a", "internal_c")}
        )
        side = reconcile_side(_surface("api_a"), index)
        assert side.exports_total == 2
        assert side.exports_declared_in_headers == 1
        assert side.exports_not_declared_in_headers == 1


class TestCheckOwnership:
    """Only the whole-product check moves off the member pass."""

    def test_exactly_public_not_exported_is_release_owned(self) -> None:
        assert release_owned_checks() == frozenset({"public_not_exported"})

    def test_an_undocumented_export_stays_a_member_finding(self) -> None:
        """Its attribution *is* the exporting member, so moving it would
        change nothing but the amount of duplicated accounting code."""
        assert ChangeKind.EXPORTED_NOT_PUBLIC.value not in release_owned_checks()

    def test_the_check_names_are_the_kind_values(self) -> None:
        """The policy layer derives the owned check's name from the
        model-owned ``ChangeKind`` because ``policy -> workflows`` is
        forbidden. That is only sound while the two vocabularies agree."""
        from abicheck.buildsource.cross_source_checks import ALL_CHECKS

        kind_values = {k.value for k in ChangeKind}
        assert set(ALL_CHECKS) <= kind_values
        assert release_owned_checks() <= set(ALL_CHECKS)

    def test_outside_a_scope_every_check_is_member_owned(self) -> None:
        assert member_owned_checks({"a", "b"}) == frozenset({"a", "b"})

    def test_inside_a_scope_the_owned_checks_are_removed(self) -> None:
        with release_owned_checks_scope({"a"}):
            assert member_owned_checks({"a", "b"}) == frozenset({"b"})
        assert member_owned_checks({"a", "b"}) == frozenset({"a", "b"})

    def test_the_scope_is_restored_after_an_exception(self) -> None:
        with pytest.raises(RuntimeError), release_owned_checks_scope({"a"}):
            raise RuntimeError("boom")
        assert member_owned_checks({"a"}) == frozenset({"a"})

    def test_the_scope_reaches_a_copied_context(self) -> None:
        """How it reaches a parallel member worker: the fan-out submits each
        task with a copy of the calling thread's context."""
        from contextvars import copy_context

        with release_owned_checks_scope({"a"}):
            ctx = copy_context()
        assert ctx.run(member_owned_checks, {"a", "b"}) == frozenset({"b"})


class TestSharedFindingFold:
    """One product fact, rendered once (scenario 7), with attribution."""

    @staticmethod
    def _entry(library: str, *findings: dict[str, object]) -> dict[str, object]:
        return {"library": library, "findings": [dict(f) for f in findings]}

    TYPE_CHANGE = {
        "kind": "type_size_changed",
        "symbol": "Cfg",
        "old_value": "4",
        "new_value": "8",
        "description": "struct Cfg grew",
    }

    def test_a_shared_type_change_is_folded_to_one(self) -> None:
        entries = [
            self._entry("libA.so", self.TYPE_CHANGE),
            self._entry("libB.so", self.TYPE_CHANGE),
        ]
        fold = dedupe_shared_member_findings(entries)
        assert len(fold.shared) == 1
        assert fold.shared[0].affected_libraries == ("libA.so", "libB.so")
        assert entries[0]["findings"] == []
        assert entries[1]["findings"] == []

    def test_the_member_entry_discloses_what_was_folded(self) -> None:
        entries = [
            self._entry("libA.so", self.TYPE_CHANGE),
            self._entry("libB.so", self.TYPE_CHANGE),
        ]
        dedupe_shared_member_findings(entries)
        assert entries[0]["product_level_findings"] == 1

    def test_a_finding_only_one_member_reports_is_untouched(self) -> None:
        own = {
            "kind": "func_removed",
            "symbol": "only_in_a",
            "description": "gone",
            "old_value": None,
            "new_value": None,
        }
        entries = [self._entry("libA.so", own), self._entry("libB.so")]
        fold = dedupe_shared_member_findings(entries)
        assert fold.shared == ()
        assert entries[0]["findings"] == [own]

    def test_differing_values_are_not_the_same_fact(self) -> None:
        other = dict(self.TYPE_CHANGE, new_value="16")
        entries = [
            self._entry("libA.so", self.TYPE_CHANGE),
            self._entry("libB.so", other),
        ]
        assert dedupe_shared_member_findings(entries).shared == ()

    def test_the_fold_is_order_independent(self) -> None:
        a = self._entry("libA.so", self.TYPE_CHANGE)
        b = self._entry("libB.so", self.TYPE_CHANGE)
        forward = dedupe_shared_member_findings([dict(a), dict(b)])
        backward = dedupe_shared_member_findings([dict(b), dict(a)])
        assert forward.shared == backward.shared

    @pytest.mark.parametrize("members", [2, 3, 8, 28])
    def test_the_rendered_count_does_not_grow_with_members(self, members: int) -> None:
        """Scenario 17, as the bug class's own invariant: adding members that
        share the surface must not multiply the reported evidence."""
        entries = [self._entry(f"lib{i}.so", self.TYPE_CHANGE) for i in range(members)]
        fold = dedupe_shared_member_findings(entries)
        assert len(fold.shared) == 1
        assert sum(len(e["findings"]) for e in entries) == 0
        assert len(fold.shared[0].affected_libraries) == members


class TestReportSection:
    """The section states one set of numbers for every format (scenario 16)."""

    def _terms(self):
        index = build_bundle_export_index(
            "new", {"libA.so": _member("api_a"), "libB.so": _member("api_b")}
        )
        result = reconcile_release(_surface("api_a", "api_b", "gone"), index)
        return compute_release_public_surface(
            result, acquisition={"acquisitions": 2, "reuses": 0, "keys": []}
        )

    def test_the_json_projection_carries_every_required_number(self) -> None:
        doc = self._terms().to_dict()
        side = doc["sides"]["new"]
        for key in (
            "public_declarations_with_export_obligation",
            "satisfied_by_bundle_exports",
            "missing_from_bundle",
            "exports_total",
            "exports_declared_in_headers",
            "exports_not_declared_in_headers",
            "coverage_complete",
            "acquisition_key",
        ):
            assert key in side, key
        assert doc["acquisition"]["acquisitions"] == 2

    def test_the_markdown_render_states_the_same_numbers(self) -> None:
        terms = self._terms()
        text = render_release_public_surface_markdown(terms)
        side = terms.to_dict()["sides"]["new"]
        assert "## Release public surface" in text
        assert f"{side['public_declarations_with_export_obligation']} public" in text
        assert "Header acquisitions: 2" in text

    def test_an_unevaluated_stage_renders_nothing(self) -> None:
        assert (
            render_release_public_surface_markdown(compute_release_public_surface(None))
            == ""
        )

    def test_the_section_is_json_serialisable_and_deterministic(self) -> None:
        import json

        doc = self._terms().to_dict()
        assert json.dumps(doc, sort_keys=True) == json.dumps(
            self._terms().to_dict(), sort_keys=True
        )


class TestSurfaceRoundTrip:
    """The surface survives persistence unchanged (scenario 15's value half)."""

    def test_to_dict_from_dict_is_lossless(self) -> None:
        surface = ReleasePublicSurface(
            acquisition_key="key",
            side="old",
            obligations=(
                PublicObligation("s1", "n1", "function", "h.h:3"),
                PublicObligation("s2", "n2", "variable"),
            ),
            declared_symbols=frozenset({"s1", "s2", "s3"}),
            header_count=2,
            type_names=("Cfg",),
        )
        assert ReleasePublicSurface.from_dict(surface.to_dict()) == surface

    def test_an_unresolved_surface_round_trips_with_its_reason(self) -> None:
        surface = unresolved_surface(
            acquisition_key="k", side="new", reason="acquisition failed: boom"
        )
        back = ReleasePublicSurface.from_dict(surface.to_dict())
        assert back.resolvable is False
        assert back.unresolved_reason == "acquisition failed: boom"

    def test_a_malformed_obligation_list_does_not_crash_a_reader(self) -> None:
        back = ReleasePublicSurface.from_dict(
            {"acquisition_key": "k", "side": "new", "obligations": ["not a mapping"]}
        )
        assert back.obligations == ()


class TestBundleFactsSchema:
    """Schema-version discipline and prior-schema loading (scenario 15)."""

    @staticmethod
    def _facts(surface: ReleasePublicSurface | None):
        from abicheck.model.bundle_facts import BundleFacts

        return BundleFacts(per_library_snapshots={}, public_surface=surface)

    def test_a_document_without_a_surface_keeps_its_old_version(self) -> None:
        from abicheck.storage.bundle_facts_codec import bundle_facts_to_dict

        doc = bundle_facts_to_dict(self._facts(None))
        assert doc["schema_version"] == 2
        assert "public_surface" not in doc

    def test_a_document_with_a_surface_declares_version_4(self) -> None:
        from abicheck.storage.bundle_facts_codec import bundle_facts_to_dict

        doc = bundle_facts_to_dict(self._facts(_surface("api_a", side="old")))
        assert doc["schema_version"] == 4

    def test_the_document_round_trips(self) -> None:
        from abicheck.storage.bundle_facts_codec import (
            bundle_facts_from_dict,
            bundle_facts_to_dict,
        )

        doc = bundle_facts_to_dict(self._facts(_surface("api_a", side="old")))
        assert bundle_facts_to_dict(bundle_facts_from_dict(doc)) == doc

    def test_a_prior_schema_document_still_loads(self) -> None:
        from abicheck.storage.bundle_facts_codec import bundle_facts_from_dict

        facts = bundle_facts_from_dict(
            {
                "artifact_type": "abicheck.bundle-facts",
                "schema_version": 2,
                "per_library_snapshots": {},
            }
        )
        assert facts.public_surface is None

    def test_a_surface_under_a_prior_version_is_refused(self) -> None:
        """No silent reinterpretation: a reader that cannot honor the block
        must refuse the document rather than drop the product contract and
        reconcile the members against nothing."""
        from abicheck.storage.bundle_facts_codec import bundle_facts_from_dict

        with pytest.raises(ValueError, match="public_surface"):
            bundle_facts_from_dict(
                {
                    "artifact_type": "abicheck.bundle-facts",
                    "schema_version": 3,
                    "per_library_snapshots": {},
                    "public_surface": {"acquisition_key": "k", "side": "old"},
                }
            )

    def test_the_project_snapshot_importer_refuses_a_v4_document(self) -> None:
        """Fail closed, don't drop the contract. The ``ProjectSnapshot``
        import adapter has no composition section for ``public_surface``, so
        it refuses a schema-4 document rather than importing one whose
        recorded contract it would silently discard -- which would leave the
        imported members reconciled against nothing."""
        from abicheck.errors import IncompatibleSnapshotSchemaError
        from abicheck.model.bundle_facts import PUBLIC_SURFACE_SCHEMA_VERSION
        from abicheck.storage.import_bundle_facts import (
            _BUNDLE_FACTS_SCHEMA_VERSION,
        )

        assert _BUNDLE_FACTS_SCHEMA_VERSION < PUBLIC_SURFACE_SCHEMA_VERSION
        from abicheck.serialization import SCHEMA_VERSION
        from abicheck.storage import InMemoryObjectStore
        from abicheck.storage.import_bundle_facts import import_bundle_facts

        with pytest.raises(IncompatibleSnapshotSchemaError, match="newer than"):
            import_bundle_facts(
                {
                    "artifact_type": "abicheck.bundle-facts",
                    "schema_version": PUBLIC_SURFACE_SCHEMA_VERSION,
                    "per_library_snapshots": {},
                    "public_surface": {"acquisition_key": "k", "side": "old"},
                },
                store=InMemoryObjectStore(),
                max_known_schema_version=SCHEMA_VERSION,
            )

    def test_a_wrong_shaped_surface_is_refused(self) -> None:
        from abicheck.storage.bundle_facts_codec import bundle_facts_from_dict

        with pytest.raises(ValueError, match="must be a mapping"):
            bundle_facts_from_dict(
                {
                    "artifact_type": "abicheck.bundle-facts",
                    "schema_version": 4,
                    "per_library_snapshots": {},
                    "public_surface": ["nope"],
                }
            )


class TestScalarPathUnchanged:
    """A one-member release keeps the per-member answer (scenario 14)."""

    def test_a_single_member_release_owns_no_check_at_release_level(self) -> None:
        """There is no union to take and no sibling to resolve against, so
        the per-member answer is already exactly right -- which is what keeps
        a one-member package and the scalar path agreeing."""
        from abicheck.cli_compare_release_pairwise import _release_owned_crosschecks

        assert _release_owned_crosschecks() == frozenset({"public_not_exported"})

    def test_the_stage_is_inert_below_two_members(self) -> None:
        from abicheck.workflows.release_public_surface import (
            reconcile_release_public_surface,
        )

        stage = reconcile_release_public_surface(
            [
                {
                    "library": "libA.so",
                    "verdict": "NO_CHANGE",
                    "_new_bundle_evidence": _member("api_a"),
                }
            ],
            expected_members=["libA.so"],
            old_headers=[Path("/inc")],
            new_headers=[Path("/inc")],
            old_includes=[],
            new_includes=[],
            lang="c",
        )
        assert stage.reconciliation is None
        assert stage.findings == ()
        assert stage.ledger.total_acquisitions == 0

    def test_the_stage_is_inert_without_headers(self) -> None:
        from abicheck.workflows.release_public_surface import (
            reconcile_release_public_surface,
        )

        stage = reconcile_release_public_surface(
            [
                {
                    "library": "libA.so",
                    "verdict": "NO_CHANGE",
                    "_new_bundle_evidence": _member("a"),
                },
                {
                    "library": "libB.so",
                    "verdict": "NO_CHANGE",
                    "_new_bundle_evidence": _member("b"),
                },
            ],
            expected_members=["libA.so", "libB.so"],
            old_headers=[],
            new_headers=[],
            old_includes=[],
            new_includes=[],
            lang="c",
        )
        assert stage.reconciliation is None

    def test_a_non_member_entry_never_narrows_coverage(self) -> None:
        """A support-promise finding's entry is keyed by the promise, not a
        DSO. Counting it as a member with no export evidence would mark the
        whole reconciliation incomplete, which suppresses every real
        missing-export finding -- the defect this parameter exists for."""
        from abicheck.workflows.release_public_surface import (
            reconcile_release_public_surface,
        )

        entries = [
            {
                "library": "libA.so",
                "verdict": "NO_CHANGE",
                "_new_bundle_evidence": _member("api_a"),
                "_old_bundle_evidence": _member("api_a"),
            },
            {
                "library": "libB.so",
                "verdict": "NO_CHANGE",
                "_new_bundle_evidence": _member("api_b"),
                "_old_bundle_evidence": _member("api_b"),
            },
            # Not a library at all.
            {"library": "promise:libgone.so", "verdict": "BREAKING", "breaking": 1},
        ]
        stage = reconcile_release_public_surface(
            entries,
            expected_members=["libA.so", "libB.so"],
            old_headers=[],
            new_headers=[],
            old_includes=[],
            new_includes=[],
            lang="c",
        )
        # No headers, so the stage is inert -- but the membership filter is
        # what the next assertion really exercises, on the index directly.
        assert stage.reconciliation is None
        from abicheck.workflows.release_public_surface import _member_evidence

        evidence, failed = _member_evidence(entries, "new", ["libA.so", "libB.so"])
        assert sorted(evidence) == ["libA.so", "libB.so"]
        assert failed == {}

    @pytest.mark.parametrize(
        "verdict", ["ERROR", "error", "failed", "not_comparable", "unsupported"]
    )
    def test_every_failure_verdict_counts_as_an_unread_member(
        self, verdict: str
    ) -> None:
        """`member_error_entry`'s whole vocabulary, not just "ERROR": a
        member recorded under any of them produced no export evidence, so
        an absent symbol cannot be concluded missing from the product."""
        from abicheck.workflows.release_public_surface import _member_evidence

        _, failed = _member_evidence(
            [{"library": "libB.so", "verdict": verdict, "reason": "why"}],
            "new",
            ["libB.so"],
        )
        assert failed == {"libB.so": "why"}


class TestReleaseLevelFindingsStillGate:
    """Moving a check's owner must not weaken what it gates."""

    def _stage(self, *, missing: bool):
        from abicheck.workflows.release_public_surface import ReleaseSurfaceStage

        index = build_bundle_export_index("new", {"libA.so": _member("api_a")})
        surface = _surface("api_a", "gone") if missing else _surface("api_a")
        return ReleaseSurfaceStage(
            reconciliation=reconcile_release(surface, index),
            ledger=SurfaceAcquisitionLedger(),
            old_surface=None,
            new_surface=surface,
        )

    def test_a_release_level_finding_contributes_a_verdict(self) -> None:
        from abicheck.workflows.release_public_surface import release_surface_verdict

        assert release_surface_verdict(self._stage(missing=True)) != "NO_CHANGE"

    def test_a_clean_stage_contributes_nothing(self) -> None:
        from abicheck.workflows.release_public_surface import release_surface_verdict

        assert release_surface_verdict(self._stage(missing=False)) == "NO_CHANGE"

    def test_a_resolved_finding_does_not_drive_the_verdict(self) -> None:
        """The same exclusion `checker._compute_verdict_for` applies to a
        member comparison's own cross-source findings: a finding this release
        *fixed* is reported, not charged."""
        from abicheck.workflows.release_public_surface import release_surface_verdict

        result = reconcile_release(
            _surface("api_a", "back"),
            build_bundle_export_index("new", {"libA.so": _member("api_a", "back")}),
            old_surface=_surface("api_a", "back", side="old"),
            old_index=build_bundle_export_index("old", {"libA.so": _member("api_a")}),
        )
        from abicheck.workflows.release_public_surface import ReleaseSurfaceStage

        stage = ReleaseSurfaceStage(result, SurfaceAcquisitionLedger(), None, None)
        assert result.findings
        assert (
            result.findings[0].cross_source_evolution is CrossSourceEvolution.RESOLVED
        )
        assert release_surface_verdict(stage) == "NO_CHANGE"

    def test_it_reaches_the_severity_aware_exit_too(self) -> None:
        """The severity exit is aggregated per library, and a release-level
        finding belongs to no library -- so without its own fold a clean-
        per-member release with a missing product export would exit 0 while
        the verdict said otherwise."""
        from abicheck.policy.severity import SeverityConfig, SeverityLevel
        from abicheck.workflows.release_public_surface import (
            release_surface_severity_exit,
        )

        # `public_not_exported` resolves to the risk category, so gate that
        # one to error -- against the default config the contribution is
        # legitimately 0 and the assertion would say nothing.
        severity = SeverityConfig(potential_breaking=SeverityLevel.ERROR)
        assert release_surface_severity_exit(self._stage(missing=True), severity) > 0
        assert release_surface_severity_exit(self._stage(missing=False), severity) == 0

    def test_no_severity_setting_is_a_no_op(self) -> None:
        from abicheck.workflows.release_public_surface import (
            release_surface_severity_exit,
        )

        assert release_surface_severity_exit(self._stage(missing=True), None) == 0


class TestTheStageNeverRaises:
    """A reporting stage must not abort a release over its own inputs."""

    def test_a_missing_header_input_is_an_unresolved_surface(
        self, tmp_path: Path
    ) -> None:
        from abicheck.workflows.release_public_surface import (
            reconcile_release_public_surface,
        )

        entries = [
            {
                "library": "libA.so",
                "verdict": "NO_CHANGE",
                "_new_bundle_evidence": _member("api_a"),
            },
            {
                "library": "libB.so",
                "verdict": "NO_CHANGE",
                "_new_bundle_evidence": _member("api_b"),
            },
        ]
        stage = reconcile_release_public_surface(
            entries,
            expected_members=["libA.so", "libB.so"],
            old_headers=[],
            new_headers=[tmp_path / "does-not-exist.h"],
            old_includes=[],
            new_includes=[],
            lang="c",
        )
        assert stage.reconciliation is not None
        assert stage.new_surface is not None
        assert stage.new_surface.resolvable is False
        assert "could not be expanded" in (stage.new_surface.unresolved_reason or "")
        assert stage.findings == ()

    def test_an_empty_header_directory_is_too(self, tmp_path: Path) -> None:
        from abicheck.workflows.release_public_surface import (
            reconcile_release_public_surface,
        )

        empty = tmp_path / "include"
        empty.mkdir()
        stage = reconcile_release_public_surface(
            [
                {
                    "library": "libA.so",
                    "verdict": "NO_CHANGE",
                    "_new_bundle_evidence": _member("api_a"),
                },
                {
                    "library": "libB.so",
                    "verdict": "NO_CHANGE",
                    "_new_bundle_evidence": _member("api_b"),
                },
            ],
            expected_members=["libA.so", "libB.so"],
            old_headers=[],
            new_headers=[empty],
            old_includes=[],
            new_includes=[],
            lang="c",
        )
        assert stage.new_surface is not None
        assert stage.new_surface.resolvable is False
        # Nothing was parsed, so nothing is counted as acquired.
        assert stage.ledger.total_acquisitions == 0


class TestEveryContainerFormatSurvivesTheCompactProjection:
    """The release fan-out keeps a *compact* per-member evidence object, and
    the export index must read exports off it on every platform.

    The class, not the one platform: ``BundleSignatureEvidence`` carries an
    ``elf`` field and nothing for PE or Mach-O, so an index deriving exports
    from container metadata saw **no exports at all** for a Windows or macOS
    member -- indistinguishable from a member that genuinely exports
    nothing. The release contract reconciliation therefore read coverage as
    *incomplete* on both platforms and suppressed every real missing-export
    finding, while Linux was unaffected. Caught by this PR's own
    ``integration-tests`` matrix on ``macos-latest`` and ``windows-latest``,
    not by any Linux run.
    """

    @staticmethod
    def _snapshot_with(**platform: object):
        from abicheck.model import AbiSnapshot

        return AbiSnapshot(library="libX", version="1", **platform)  # type: ignore[arg-type]

    def _elf(self):
        return self._snapshot_with(
            elf=ElfMetadata(
                soname="",
                needed=[],
                symbols=[ElfSymbol(name="api_a", is_default=True)],
                imports=[],
            )
        )

    def _pe(self):
        from abicheck.pe_metadata import PeExport, PeMetadata

        return self._snapshot_with(
            pe=PeMetadata(exports=[PeExport(name="api_a", ordinal=1)])
        )

    def _macho(self):
        from abicheck.macho_metadata import MachoExport, MachoMetadata

        return self._snapshot_with(
            macho=MachoMetadata(exports=[MachoExport(name="api_a")])
        )

    @pytest.mark.parametrize("platform", ["elf", "pe", "macho"])
    def test_the_compact_evidence_carries_that_platforms_exports(
        self, platform: str
    ) -> None:
        from abicheck.workflows.bundle_symbol_status import (
            build_bundle_signature_evidence,
        )

        snapshot = getattr(self, f"_{platform}")()
        evidence = build_bundle_signature_evidence(snapshot)
        assert member_export_names(evidence) == frozenset({"api_a"})

    @pytest.mark.parametrize("platform", ["elf", "pe", "macho"])
    def test_the_index_is_complete_for_that_platform(self, platform: str) -> None:
        """The consequence that actually bit: an unreadable member makes
        coverage incomplete, which suppresses the findings this whole
        workstream exists to produce."""
        from abicheck.workflows.bundle_symbol_status import (
            build_bundle_signature_evidence,
        )

        snapshot = getattr(self, f"_{platform}")()
        index = build_bundle_export_index(
            "new", {"libX": build_bundle_signature_evidence(snapshot)}
        )
        assert index.complete is True
        assert index.members_without_exports == ()
        assert index.providers("api_a") == ("libX",)

    @pytest.mark.parametrize("platform", ["elf", "pe", "macho"])
    def test_the_compact_form_agrees_with_the_full_snapshot(
        self, platform: str
    ) -> None:
        """A compact member and a full one must index identically -- the
        fan-out picks between them for memory reasons only (JUnit and
        ``--bundle-facts-out`` keep the full snapshot), and which one a run
        happened to keep must never change a finding."""
        from abicheck.workflows.bundle_symbol_status import (
            build_bundle_signature_evidence,
        )

        snapshot = getattr(self, f"_{platform}")()
        assert member_export_names(
            build_bundle_signature_evidence(snapshot)
        ) == member_export_names(snapshot)

    def test_a_snapshot_with_no_container_still_reports_no_evidence(self) -> None:
        """`None` stays "nothing was observed", never "exports nothing" --
        the distinction the coverage gate rests on."""
        from abicheck.workflows.bundle_symbol_status import (
            build_bundle_signature_evidence,
        )

        evidence = build_bundle_signature_evidence(self._snapshot_with())
        assert member_export_names(evidence) is None
        index = build_bundle_export_index("new", {"libX": evidence})
        assert index.complete is False
        assert index.members_without_exports == ("libX",)

    def test_the_projection_is_the_canonical_one(self) -> None:
        """Not a second notion of "exported": the compact projection must
        equal `model.export_index`'s own, including its default/unversioned
        ELF rule."""
        from abicheck.model.export_index import (
            build_raw_export_index,
            default_versioned_names,
        )
        from abicheck.workflows.bundle_symbol_status import (
            build_bundle_signature_evidence,
        )

        snapshot = self._snapshot_with(
            elf=ElfMetadata(
                soname="",
                needed=[],
                symbols=[
                    ElfSymbol(name="api_a", is_default=True),
                    ElfSymbol(name="api_old", version="V1", is_default=False),
                ],
                imports=[],
            )
        )
        raw = build_raw_export_index(snapshot)
        assert raw is not None
        assert member_export_names(
            build_bundle_signature_evidence(snapshot)
        ) == default_versioned_names(raw)
