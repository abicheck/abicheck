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

"""Workstream E slice S3: multi-source contract conflicts with provenance.

Each of the three cases (exported-but-undeclared, manifest narrowing since
baseline, package-claim-vs-binary) must record a real conflict naming BOTH
disagreeing sources' own claims -- never silently resolved to one side
(ADR-067 "record before disposing"). These tests pin exactly that: every
conflict carries >= 2 ``ConflictSourceClaim`` entries whose ``source_kind``s
differ, and neither claim is dropped or overwritten by the other.
"""

from __future__ import annotations

import pytest

from abicheck.debian_symbols import DebianSymbolEntry, DebianSymbolsFile
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolType
from abicheck.export_surface import compute_export_surface
from abicheck.model import AbiSnapshot, Function, Param, ScopeOrigin, Visibility
from abicheck.model.contract_conflicts import (
    ALL_CONFLICT_KINDS,
    CONFLICT_EXPORTED_BUT_UNDECLARED,
    CONFLICT_MANIFEST_NARROWED_SINCE_BASELINE,
    CONFLICT_PACKAGE_BINARY_MISMATCH,
    ConflictSourceClaim,
    ContractSourceConflict,
    conflicts_to_dicts,
)
from abicheck.policy.contract_conflicts import (
    detect_exported_but_undeclared,
    detect_manifest_narrowing_since_baseline,
)
from abicheck.workflows.contract_conflicts import detect_package_binary_mismatch


def _fn(name, mangled, ret="void", params=(), vis=Visibility.PUBLIC):
    return Function(
        name=name,
        mangled=mangled,
        return_type=ret,
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params)],
        visibility=vis,
        origin=ScopeOrigin.UNKNOWN,
    )


# ---------------------------------------------------------------------------
# Shape tests: a conflict is never a single-source claim.
# ---------------------------------------------------------------------------


class TestContractSourceConflictShape:
    def test_requires_at_least_two_sources(self) -> None:
        with pytest.raises(ValueError, match="at least two sources"):
            ContractSourceConflict(
                conflict_kind=CONFLICT_EXPORTED_BUT_UNDECLARED,
                entity="foo",
                sources=(ConflictSourceClaim(source_kind="export_table", claim="x"),),
                reason_code="r",
            )

    def test_rejects_unknown_kind(self) -> None:
        with pytest.raises(ValueError, match="conflict_kind"):
            ContractSourceConflict(
                conflict_kind="not_a_real_kind",
                entity="foo",
                sources=(
                    ConflictSourceClaim(source_kind="a", claim="x"),
                    ConflictSourceClaim(source_kind="b", claim="y"),
                ),
                reason_code="r",
            )

    def test_round_trips_through_to_dict_from_dict(self) -> None:
        c = ContractSourceConflict(
            conflict_kind=CONFLICT_EXPORTED_BUT_UNDECLARED,
            entity="foo",
            sources=(
                ConflictSourceClaim(
                    source_kind="export_table",
                    claim="binary exports symbol 'foo'",
                    detail={"symbol": "foo"},
                ),
                ConflictSourceClaim(
                    source_kind="public_header",
                    claim="no declaration matches",
                ),
            ),
            reason_code="export_unmatched_by_header_surface",
            side="new",
        )
        restored = ContractSourceConflict.from_dict(c.to_dict())
        assert restored == c
        assert conflicts_to_dicts([c])[0]["entity"] == "foo"

    def test_all_conflict_kinds_are_distinct(self) -> None:
        assert len(ALL_CONFLICT_KINDS) == 3
        assert {
            CONFLICT_EXPORTED_BUT_UNDECLARED,
            CONFLICT_MANIFEST_NARROWED_SINCE_BASELINE,
            CONFLICT_PACKAGE_BINARY_MISMATCH,
        } == ALL_CONFLICT_KINDS


# ---------------------------------------------------------------------------
# Case 1: exported-but-undeclared.
# ---------------------------------------------------------------------------


class TestExportedButUndeclared:
    def test_records_conflict_with_both_sources_claims(self) -> None:
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("known", "known")],
            elf=ElfMetadata(
                symbols=[ElfSymbol(name="known"), ElfSymbol(name="mystery")]
            ),
        )
        surf = compute_export_surface(snap)
        conflicts = detect_exported_but_undeclared(surf, side="new")

        assert len(conflicts) == 1
        c = conflicts[0]
        assert c.conflict_kind == CONFLICT_EXPORTED_BUT_UNDECLARED
        assert c.entity == "mystery"
        assert c.side == "new"
        assert len(c.sources) == 2
        kinds = {s.source_kind for s in c.sources}
        assert kinds == {"export_table", "public_header"}
        export_claim = next(s for s in c.sources if s.source_kind == "export_table")
        header_claim = next(s for s in c.sources if s.source_kind == "public_header")
        assert "mystery" in export_claim.claim
        assert "no public header declaration" in header_claim.claim
        # Neither claim is dropped or merged into the other: this is what
        # "recorded, not resolved" means for this case (ADR-067).
        assert export_claim.claim != header_claim.claim

    def test_no_conflict_when_every_export_is_declared(self) -> None:
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("known", "known")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="known")]),
        )
        surf = compute_export_surface(snap)
        assert detect_exported_but_undeclared(surf, side="new") == []

    def test_no_conflict_without_an_observed_export_table(self) -> None:
        # No export table at all -> nothing is *provably* exported-but-
        # undeclared; recording one here would invent a break from absent
        # evidence (ADR-028 D3).
        snap = AbiSnapshot(library="l", version="1", functions=[_fn("known", "known")])
        surf = compute_export_surface(snap)
        assert not surf.resolvable
        assert detect_exported_but_undeclared(surf, side="new") == []


# ---------------------------------------------------------------------------
# Case 2: manifest narrowing since baseline.
# ---------------------------------------------------------------------------


class TestManifestNarrowingSinceBaseline:
    def test_records_conflict_for_a_symbol_the_manifest_excludes(self) -> None:
        baseline = {"foo", "bar"}
        current = {"foo", "bar"}
        manifest_allowlist = frozenset({"foo"})  # 'bar' narrowed out

        conflicts = detect_manifest_narrowing_since_baseline(
            baseline, current, manifest_allowlist, side="new"
        )

        assert len(conflicts) == 1
        c = conflicts[0]
        assert c.conflict_kind == CONFLICT_MANIFEST_NARROWED_SINCE_BASELINE
        assert c.entity == "bar"
        assert c.side == "new"
        kinds = {s.source_kind for s in c.sources}
        assert kinds == {"baseline_contract", "post_manifest"}
        baseline_claim = next(
            s for s in c.sources if s.source_kind == "baseline_contract"
        )
        manifest_claim = next(s for s in c.sources if s.source_kind == "post_manifest")
        assert "declared public at baseline" in baseline_claim.claim
        assert "not present" in manifest_claim.claim

    def test_no_conflict_when_no_manifest_is_in_effect(self) -> None:
        assert detect_manifest_narrowing_since_baseline({"foo"}, {"foo"}, None) == []

    def test_empty_allowlist_narrows_everything_still_public(self) -> None:
        # An empty manifest is a real, active "commit to zero exports" --
        # distinct from None (no manifest at all) -- so it narrows every
        # symbol still declared public on both sides.
        conflicts = detect_manifest_narrowing_since_baseline(
            {"foo"}, {"foo"}, frozenset()
        )
        assert len(conflicts) == 1
        assert conflicts[0].entity == "foo"

    def test_symbol_removed_from_current_headers_is_not_a_manifest_conflict(
        self,
    ) -> None:
        # 'bar' was public at baseline but is no longer declared public in
        # the current headers at all -- an ordinary removal, not something
        # the manifest overlay did, so it must not appear here.
        conflicts = detect_manifest_narrowing_since_baseline(
            {"foo", "bar"}, {"foo"}, frozenset({"foo"})
        )
        assert conflicts == []


# ---------------------------------------------------------------------------
# Case 3: package-claim vs. contained-binary.
# ---------------------------------------------------------------------------


class TestPackageBinaryMismatch:
    def test_records_conflict_for_declared_symbol_binary_lacks(self) -> None:
        symbols_file = DebianSymbolsFile(
            library="libfoo.so.1",
            package="libfoo1",
            min_version="#MINVER#",
            symbols=[
                DebianSymbolEntry(
                    name="foo_init", version_node="Base", min_version="1.0"
                ),
            ],
        )
        elf_meta = ElfMetadata(
            soname="libfoo.so.1",
            symbols=[ElfSymbol(name="foo_other", sym_type=SymbolType.FUNC)],
        )

        conflicts = detect_package_binary_mismatch(symbols_file, elf_meta)

        assert len(conflicts) == 1
        c = conflicts[0]
        assert c.conflict_kind == CONFLICT_PACKAGE_BINARY_MISMATCH
        assert c.entity == "foo_init"
        kinds = {s.source_kind for s in c.sources}
        assert kinds == {"package_metadata", "binary"}
        pkg_claim = next(s for s in c.sources if s.source_kind == "package_metadata")
        bin_claim = next(s for s in c.sources if s.source_kind == "binary")
        assert "foo_init" in pkg_claim.claim
        assert "does not export" in bin_claim.claim

    def test_records_soname_mismatch(self) -> None:
        symbols_file = DebianSymbolsFile(
            library="libfoo.so.1",
            package="libfoo1",
            min_version="#MINVER#",
            symbols=[],
        )
        elf_meta = ElfMetadata(soname="libfoo.so.2", symbols=[])

        conflicts = detect_package_binary_mismatch(symbols_file, elf_meta)

        soname_conflicts = [c for c in conflicts if c.entity == "soname"]
        assert len(soname_conflicts) == 1
        c = soname_conflicts[0]
        kinds = {s.source_kind for s in c.sources}
        assert kinds == {"package_metadata", "binary"}
        pkg_claim = next(s for s in c.sources if s.source_kind == "package_metadata")
        bin_claim = next(s for s in c.sources if s.source_kind == "binary")
        assert "libfoo.so.1" in pkg_claim.claim
        assert "libfoo.so.2" in bin_claim.claim

    def test_no_conflict_when_package_and_binary_agree(self) -> None:
        symbols_file = DebianSymbolsFile(
            library="libfoo.so.1",
            package="libfoo1",
            min_version="#MINVER#",
            symbols=[
                DebianSymbolEntry(
                    name="foo_init", version_node="Base", min_version="1.0"
                ),
            ],
        )
        elf_meta = ElfMetadata(
            soname="libfoo.so.1",
            symbols=[ElfSymbol(name="foo_init", sym_type=SymbolType.FUNC)],
        )
        assert detect_package_binary_mismatch(symbols_file, elf_meta) == []
