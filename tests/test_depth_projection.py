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

"""ADR-063 Phase 8's ``--depth`` floor-vs-ceiling gap (Codex review, PR #1020).

``project_snapshot_to_depth`` caps an already-resolved snapshot's evidence
to what an explicit ``--depth`` requested. The general invariant this suite
states as a property, not only pinned example inputs: for any snapshot pair
and any requested depth rung, a classification's answer depends only on
evidence at or below that rung -- never on evidence embedded in the
resolved snapshot above it.

Six real, review-caught gaps are pinned by name here so they don't recur:

1. The projection was only ever wired into the typed-API/release-fan-out
   chokepoint (``classify_compare_pair``); the native ``abicheck compare``
   CLI (``cli_compare_helpers.run_compare``) calls ``compare_snapshots()``
   directly and never saw it at all.
2. The ``binary`` rung unconditionally kept structural facts (types, enums,
   typedefs, function/variable signatures), on the theory those are an
   L0/L1 (DWARF-visible) fact -- true only when the snapshot actually
   carries DWARF. A purely header-derived snapshot with no DWARF at all
   still leaked full structural evidence through a ``binary``-depth
   projection.
3. An explicit out-of-band ``--old/new-sources``/``--old/new-build-info``
   pack directory is resolved separately from the snapshot object
   ``project_pair_to_depth`` projects, so it bypassed the ceiling entirely
   even after gap 1 was fixed.
4. ``snap.contract`` (an ADR-050 ``ExtractionContract``) survived a
   ``binary``-depth projection, so ``checker.compare()`` could still raise
   a scope/profile mismatch error from two sides' original header scopes
   even though a binary-only comparison never looks at either.
5. A ``Visibility.HIDDEN`` (non-exported) function/variable was promoted to
   ``ELF_ONLY`` instead of dropped, manufacturing a false
   ``*_removed_elf_only`` finding for a declaration no real binary-only
   dump would ever have seen as a symbol.
6. ``types``/``enums`` were kept or dropped by the whole-snapshot
   ``dwarf.has_dwarf`` flag rather than per-record evidence, so an
   uninstantiated header-only record sitting alongside unrelated real DWARF
   content was incorrectly retained. **Superseded by gap 7** (see below) --
   a per-record ``DwarfMetadata.structs``/``.enums`` name check was the
   fix attempted for this gap, but it was itself one level too narrow.
7. A header-derived ``RecordType``/``EnumType`` was kept *wholesale*
   whenever DWARF merely confirmed the same-named struct/enum *existed* --
   DWARF confirming a struct's name says nothing about whether its
   *fields* agree with the header's own spelling (DWARF only ever
   backfills numeric layout onto a header-derived record, never its
   field-level type text). Only a genuinely DWARF/symbols-only snapshot
   (``from_headers is False``) may keep these wholesale; on a
   header-derived snapshot they are now always fully cleared, relying on
   the separate, untouched ``snap.dwarf`` fields (``diff_platform.
   _diff_dwarf``) to still catch a real DWARF-visible layout change.
8. A function/variable promoted from a header parser's own "declared
   public, without contrary evidence" fallback (e.g. an un-emitted inline
   constexpr customization-point object) was promoted to ``ELF_ONLY``
   the same as a genuinely exported one, manufacturing a false
   ``*_removed``/``*_removed_elf_only`` finding for a declaration no real
   binary-only dump would ever have seen as a symbol at all.
9. Nulling a ``BuildSourcePack``'s ``source_abi``/``source_graph`` between
   ``build`` and ``source`` left their own ``LayerCoverage`` rows in
   ``pack.manifest.coverage`` still claiming ``PRESENT``/``PARTIAL``, so a
   report could still claim source-ABI/source-graph evidence backed a
   comparison the depth ceiling actually excluded it from.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck import checker
from abicheck.buildsource.model import CoverageStatus, DataLayer, LayerCoverage
from abicheck.buildsource.pack import BuildSourcePack
from abicheck.buildsource.source_abi import SourceAbiSurface
from abicheck.cli import main
from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    Function,
    RecordType,
    ScopeOrigin,
    TypeField,
    Variable,
    Visibility,
)
from abicheck.model.dwarf_facts import DwarfMetadata, FieldInfo, StructLayout
from abicheck.model.elf_facts import ElfMetadata, ElfSymbol
from abicheck.model.extraction_contract import ExtractionContract
from abicheck.model.source_graph import SourceGraphSummary
from abicheck.policy.depth_projection import (
    project_build_source_pack_to_depth,
    project_snapshot_to_depth,
)
from abicheck.serialization import save_snapshot


def _headers_only_pair(*, dwarf: bool) -> tuple[AbiSnapshot, AbiSnapshot]:
    """Two snapshots differing across every header-level fact family.

    *dwarf* controls whether both sides carry real DWARF debug info --
    the axis the second pinned gap above depends on.
    """
    dwarf_meta = DwarfMetadata(has_dwarf=True) if dwarf else None

    def _snap(version: str, *, field_type: str, const_value: str) -> AbiSnapshot:
        return AbiSnapshot(
            library="lib",
            version=version,
            functions=[
                Function(
                    name="f",
                    mangled="_Z1fv",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
            variables=[
                Variable(
                    name="g",
                    mangled="g",
                    type="int",
                    visibility=Visibility.PUBLIC,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
            types=[
                RecordType(
                    name="S",
                    kind="struct",
                    size_bits=64,
                    fields=[TypeField(name="x", type=field_type)],
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
            enums=[
                EnumType(
                    name="E",
                    members=[EnumMember(name="A", value=0)],
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
            typedefs={"my_int": "int"},
            constants={"FOO": const_value},
            from_headers=True,
            dwarf=dwarf_meta,
        )

    old = _snap("1", field_type="int", const_value="1")
    new = _snap("2", field_type="int", const_value="2")
    return old, new


class TestProjectSnapshotToDepthNoOps:
    def test_none_depth_returns_the_same_object(self) -> None:
        snap = AbiSnapshot(library="lib", version="1")
        assert project_snapshot_to_depth(snap, None) is snap

    def test_unrecognized_depth_returns_the_same_object(self) -> None:
        snap = AbiSnapshot(library="lib", version="1")
        assert project_snapshot_to_depth(snap, "not-a-real-depth") is snap

    def test_never_mutates_its_argument(self) -> None:
        snap = AbiSnapshot(
            library="lib", version="1", constants={"FOO": "1"}, from_headers=True
        )
        project_snapshot_to_depth(snap, "binary")
        assert snap.constants == {"FOO": "1"}
        assert snap.from_headers is True


class TestBinaryDepthNoDwarf:
    """Pinned gap 2: no DWARF means every structural fact came from headers."""

    def test_no_header_derived_finding_survives(self) -> None:
        old, new = _headers_only_pair(dwarf=False)
        old_b = project_snapshot_to_depth(old, "binary")
        new_b = project_snapshot_to_depth(new, "binary")
        result = checker.compare(old_b, new_b)
        assert result.verdict == checker.Verdict.NO_CHANGE
        assert result.changes == []

    @pytest.mark.parametrize(
        "attr,expected",
        [
            ("types", []),
            ("enums", []),
            ("typedefs", {}),
            ("constants", {}),
            ("python_api", None),
            ("semantic_ir", None),
            ("from_headers", False),
            ("elf_only_mode", True),
        ],
    )
    def test_snapshot_level_facts_cleared(self, attr: str, expected: object) -> None:
        old, _ = _headers_only_pair(dwarf=False)
        projected = project_snapshot_to_depth(old, "binary")
        assert getattr(projected, attr) == expected

    def test_function_and_variable_signatures_cleared(self) -> None:
        old, _ = _headers_only_pair(dwarf=False)
        projected = project_snapshot_to_depth(old, "binary")
        fn = projected.functions[0]
        assert fn.return_type == "?"
        assert fn.params == []
        assert fn.visibility is Visibility.ELF_ONLY
        var = projected.variables[0]
        assert var.type == "?"
        assert var.is_const is False
        assert var.value is None
        assert var.visibility is Visibility.ELF_ONLY


class TestStructuralFactsRequireDwarfSourcing:
    """Pinned gap 7: DWARF confirming a struct/enum by *name* does not
    corroborate a header-derived RecordType/EnumType's own field-level
    spelling -- only a genuinely DWARF/symbols-only snapshot
    (``from_headers is False``) may keep ``types``/``enums``/... wholesale
    at ``binary`` depth."""

    def test_header_derived_field_diff_is_suppressed_even_with_dwarf(self) -> None:
        # DWARF confirms a struct named "S" exists (`structs={"S": ...}`),
        # but its own StructLayout carries no fields at all -- the field
        # type difference below exists only in the header-parsed
        # RecordType, so it must not survive a from_headers=True
        # binary-depth projection (the review-caught bug: retaining a
        # RecordType wholesale merely because DWARF confirmed the struct's
        # *name*, not its field-level content).
        dwarf_meta = DwarfMetadata(
            has_dwarf=True,
            structs={"S": StructLayout(name="S", byte_size=8, fields=[])},
        )
        old = AbiSnapshot(
            library="lib",
            version="1",
            types=[
                RecordType(
                    name="S",
                    kind="struct",
                    size_bits=64,
                    fields=[TypeField(name="x", type="int")],
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
            from_headers=True,
            dwarf=dwarf_meta,
        )
        new = AbiSnapshot(
            library="lib",
            version="2",
            types=[
                RecordType(
                    name="S",
                    kind="struct",
                    size_bits=64,
                    fields=[TypeField(name="x", type="double")],
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
            from_headers=True,
            dwarf=dwarf_meta,
        )
        old_b = project_snapshot_to_depth(old, "binary")
        new_b = project_snapshot_to_depth(new, "binary")
        result = checker.compare(old_b, new_b)
        assert result.verdict == checker.Verdict.NO_CHANGE
        assert result.changes == []

    def test_genuine_dwarf_struct_break_is_still_caught_via_snap_dwarf(self) -> None:
        # The identical field-type break, but expressed as a real
        # difference between each side's own DWARF StructLayout (not
        # merely the header-parsed RecordType) -- caught by the separate,
        # DWARF-native `diff_platform._diff_dwarf` detector, which reads
        # `snap.dwarf` directly and is never touched by this module.
        old_layout = StructLayout(
            name="S",
            byte_size=8,
            fields=[FieldInfo(name="x", type_name="int", byte_offset=0, byte_size=4)],
        )
        new_layout = StructLayout(
            name="S",
            byte_size=8,
            fields=[
                FieldInfo(name="x", type_name="double", byte_offset=0, byte_size=8)
            ],
        )
        old = AbiSnapshot(
            library="lib",
            version="1",
            types=[
                RecordType(
                    name="S",
                    kind="struct",
                    size_bits=64,
                    fields=[TypeField(name="x", type="int")],
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
            from_headers=True,
            dwarf=DwarfMetadata(has_dwarf=True, structs={"S": old_layout}),
        )
        new = AbiSnapshot(
            library="lib",
            version="2",
            types=[
                RecordType(
                    name="S",
                    kind="struct",
                    size_bits=64,
                    fields=[TypeField(name="x", type="int")],
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
            from_headers=True,
            dwarf=DwarfMetadata(has_dwarf=True, structs={"S": new_layout}),
        )
        old_b = project_snapshot_to_depth(old, "binary")
        new_b = project_snapshot_to_depth(new, "binary")
        result = checker.compare(old_b, new_b)
        assert result.verdict != checker.Verdict.NO_CHANGE
        assert {c.kind.value for c in result.changes} == {"struct_field_type_changed"}

    def test_dwarf_only_snapshot_keeps_types_wholesale(self) -> None:
        # from_headers=False -- a genuinely DWARF/symbols-only dump, where
        # `types` itself was populated FROM DWARF directly
        # (dwarf_snapshot.py), so no header-vs-DWARF ambiguity exists.
        old = AbiSnapshot(
            library="lib",
            version="1",
            types=[
                RecordType(
                    name="S",
                    kind="struct",
                    size_bits=64,
                    fields=[TypeField(name="x", type="int")],
                    origin=ScopeOrigin.UNKNOWN,
                )
            ],
            from_headers=False,
            dwarf=DwarfMetadata(has_dwarf=True),
        )
        new = AbiSnapshot(
            library="lib",
            version="2",
            types=[
                RecordType(
                    name="S",
                    kind="struct",
                    size_bits=64,
                    fields=[TypeField(name="x", type="double")],
                    origin=ScopeOrigin.UNKNOWN,
                )
            ],
            from_headers=False,
            dwarf=DwarfMetadata(has_dwarf=True),
        )
        old_b = project_snapshot_to_depth(old, "binary")
        new_b = project_snapshot_to_depth(new, "binary")
        result = checker.compare(old_b, new_b)
        assert result.verdict == checker.Verdict.BREAKING
        assert {c.kind.value for c in result.changes} == {"type_field_type_changed"}

    def test_header_only_constant_still_cleared_with_dwarf_present(self) -> None:
        old, new = _headers_only_pair(dwarf=True)
        old_b = project_snapshot_to_depth(old, "binary")
        new_b = project_snapshot_to_depth(new, "binary")
        result = checker.compare(old_b, new_b)
        # The pair differs only in `constants` (a header-only fact, always
        # cleared) -- `types`/`enums`/`typedefs` are identical between
        # old/new here, so this doesn't exercise the from_headers gate
        # above; it only pins that a from_headers=True snapshot's constants
        # are still cleared with DWARF present.
        assert result.verdict == checker.Verdict.NO_CHANGE


class TestDepthLadderMonotonicity:
    """Each rung sees strictly more than the one below it, never less."""

    @pytest.mark.parametrize("depth", ["headers", "build", "source"])
    def test_header_level_fact_survives_at_or_above_headers(self, depth: str) -> None:
        old, new = _headers_only_pair(dwarf=False)
        old_p = project_snapshot_to_depth(old, depth)
        new_p = project_snapshot_to_depth(new, depth)
        result = checker.compare(old_p, new_p)
        assert result.verdict != checker.Verdict.NO_CHANGE

    def test_header_level_fact_does_not_survive_at_binary(self) -> None:
        old, new = _headers_only_pair(dwarf=False)
        old_p = project_snapshot_to_depth(old, "binary")
        new_p = project_snapshot_to_depth(new, "binary")
        result = checker.compare(old_p, new_p)
        assert result.verdict == checker.Verdict.NO_CHANGE


class TestSurfaceGraphIsAHeaderFact:
    """``surface_graph`` is an L2 (header-only) fact -- ``_attach_header_graph``'s
    own docstring -- not an L4/L5 one; an earlier version of this module gated
    it to ``source`` on the wrong assumption (review-caught, same PR)."""

    def _snap_with_graph(self) -> AbiSnapshot:
        graph = SourceGraphSummary()
        return AbiSnapshot(
            library="lib", version="1", from_headers=True, surface_graph=graph
        )

    def test_cleared_below_headers(self) -> None:
        snap = self._snap_with_graph()
        projected = project_snapshot_to_depth(snap, "binary")
        assert projected.surface_graph is None

    @pytest.mark.parametrize("depth", ["headers", "build", "source"])
    def test_kept_at_or_above_headers(self, depth: str) -> None:
        snap = self._snap_with_graph()
        projected = project_snapshot_to_depth(snap, depth)
        assert projected.surface_graph is not None


def _invoke(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(main, list(args))
    return result.exit_code, result.output


class TestNativeCliComparePath:
    """Pinned gap 1: the native ``compare`` CLI must apply the ceiling too."""

    def _write_pair(self, tmp_path: Path) -> tuple[Path, Path]:
        old = AbiSnapshot(
            library="lib", version="1", constants={"FOO": "1"}, from_headers=True
        )
        new = AbiSnapshot(
            library="lib", version="2", constants={"FOO": "2"}, from_headers=True
        )
        old_path = tmp_path / "old.json"
        new_path = tmp_path / "new.json"
        save_snapshot(old, old_path)
        save_snapshot(new, new_path)
        return old_path, new_path

    def test_depth_binary_hides_the_header_only_break(self, tmp_path: Path) -> None:
        old_path, new_path = self._write_pair(tmp_path)
        code, _ = _invoke("compare", str(old_path), str(new_path), "--depth", "binary")
        assert code == 0

    def test_depth_headers_still_reports_it(self, tmp_path: Path) -> None:
        old_path, new_path = self._write_pair(tmp_path)
        code, _ = _invoke("compare", str(old_path), str(new_path), "--depth", "headers")
        assert code == 2

    def test_no_depth_flag_still_reports_it(self, tmp_path: Path) -> None:
        old_path, new_path = self._write_pair(tmp_path)
        code, _ = _invoke("compare", str(old_path), str(new_path))
        assert code == 2


class TestExtractionContractCleared:
    """Pinned gap 4: a stale ``contract`` must not survive a projection."""

    def test_contract_cleared_below_headers(self) -> None:
        snap = AbiSnapshot(
            library="lib",
            version="1",
            from_headers=True,
            contract=ExtractionContract(profile_fingerprint="p", scope_fingerprint="s"),
        )
        projected = project_snapshot_to_depth(snap, "binary")
        assert projected.contract is None

    @pytest.mark.parametrize("depth", ["headers", "build", "source"])
    def test_contract_kept_at_or_above_headers(self, depth: str) -> None:
        snap = AbiSnapshot(
            library="lib",
            version="1",
            from_headers=True,
            contract=ExtractionContract(profile_fingerprint="p", scope_fingerprint="s"),
        )
        projected = project_snapshot_to_depth(snap, depth)
        assert projected.contract is not None


class TestHiddenVisibilityDropped:
    """Pinned gap 5: HIDDEN is dropped, never promoted to ELF_ONLY."""

    def _snap(self) -> AbiSnapshot:
        return AbiSnapshot(
            library="lib",
            version="1",
            from_headers=True,
            functions=[
                Function(
                    name="pub",
                    mangled="_Z3pubv",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                ),
                Function(
                    name="hid",
                    mangled="_Z3hidv",
                    return_type="int",
                    visibility=Visibility.HIDDEN,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                ),
            ],
            variables=[
                Variable(
                    name="pub_v",
                    mangled="pub_v",
                    type="int",
                    visibility=Visibility.PUBLIC,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                ),
                Variable(
                    name="hid_v",
                    mangled="hid_v",
                    type="int",
                    visibility=Visibility.HIDDEN,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                ),
            ],
        )

    def test_hidden_function_and_variable_dropped(self) -> None:
        projected = project_snapshot_to_depth(self._snap(), "binary")
        assert [f.name for f in projected.functions] == ["pub"]
        assert [v.name for v in projected.variables] == ["pub_v"]

    def test_surviving_public_declarations_still_demoted_to_elf_only(self) -> None:
        projected = project_snapshot_to_depth(self._snap(), "binary")
        assert projected.functions[0].visibility is Visibility.ELF_ONLY
        assert projected.variables[0].visibility is Visibility.ELF_ONLY

    def test_no_false_removal_finding_from_a_hidden_declaration(self) -> None:
        old = self._snap()
        new = self._snap()
        new.functions = [f for f in new.functions if f.name != "hid"]
        new.variables = [v for v in new.variables if v.name != "hid_v"]
        old_b = project_snapshot_to_depth(old, "binary")
        new_b = project_snapshot_to_depth(new, "binary")
        result = checker.compare(old_b, new_b)
        assert result.verdict == checker.Verdict.NO_CHANGE
        assert result.changes == []


class TestExportTableGatesVisibilityPromotion:
    """Pinned gap 8: a header-declared-``PUBLIC``-without-evidence
    declaration must not be promoted to ``ELF_ONLY`` unless the platform's
    own export table actually confirms it."""

    def _snap(self, version: str, *, include_unconfirmed: bool) -> AbiSnapshot:
        variables = [
            Variable(
                name="pub_v",
                mangled="pub_v",
                type="int",
                visibility=Visibility.PUBLIC,
                origin=ScopeOrigin.PUBLIC_HEADER,
            )
        ]
        if include_unconfirmed:
            # A header parser's own "declared public, without contrary
            # evidence" fallback (dumper_castxml._variable_visibility's
            # un-emitted-CPO case) -- PUBLIC, but the compiler never
            # actually emitted a symbol for it, so it's absent below.
            variables.append(
                Variable(
                    name="cpo",
                    mangled="cpo",
                    type="int",
                    visibility=Visibility.PUBLIC,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            )
        elf = ElfMetadata(symbols=[ElfSymbol(name="pub_v", is_default=True)])
        return AbiSnapshot(
            library="lib",
            version=version,
            from_headers=True,
            variables=variables,
            elf=elf,
        )

    def test_unconfirmed_declaration_is_dropped(self) -> None:
        projected = project_snapshot_to_depth(
            self._snap("1", include_unconfirmed=True), "binary"
        )
        assert [v.name for v in projected.variables] == ["pub_v"]

    def test_confirmed_declaration_still_promoted(self) -> None:
        projected = project_snapshot_to_depth(
            self._snap("1", include_unconfirmed=True), "binary"
        )
        assert projected.variables[0].visibility is Visibility.ELF_ONLY

    def test_removing_an_unconfirmed_declaration_is_not_reported(self) -> None:
        old = self._snap("1", include_unconfirmed=True)
        new = self._snap("2", include_unconfirmed=False)
        old_b = project_snapshot_to_depth(old, "binary")
        new_b = project_snapshot_to_depth(new, "binary")
        result = checker.compare(old_b, new_b)
        assert result.verdict == checker.Verdict.NO_CHANGE
        assert result.changes == []

    def test_no_platform_table_falls_back_to_looser_behavior(self) -> None:
        # A synthetic/incomplete snapshot with no elf/pe/macho block at all
        # keeps the pre-existing (looser) behavior rather than being
        # stripped to nothing -- `_exported_symbol_names` returns `None`,
        # not an empty set, precisely to make this distinction.
        snap = AbiSnapshot(
            library="lib",
            version="1",
            from_headers=True,
            variables=[
                Variable(
                    name="cpo",
                    mangled="cpo",
                    type="int",
                    visibility=Visibility.PUBLIC,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
        )
        projected = project_snapshot_to_depth(snap, "binary")
        assert [v.name for v in projected.variables] == ["cpo"]
        assert projected.variables[0].visibility is Visibility.ELF_ONLY


class TestBuildSourcePackCoverageProjection:
    """Pinned gap 9: a projected-away L4/L5 payload's own coverage row must
    not still claim ``PRESENT``/``PARTIAL``."""

    def _pack(self) -> BuildSourcePack:
        pack = BuildSourcePack(root=Path("/tmp/pack"))
        pack.source_abi = SourceAbiSurface()
        pack.source_graph = SourceGraphSummary()
        pack.manifest.coverage = [
            LayerCoverage(
                layer=DataLayer.L3_BUILD.value, status=CoverageStatus.PRESENT
            ),
            LayerCoverage(
                layer=DataLayer.L4_SOURCE_ABI.value, status=CoverageStatus.PRESENT
            ),
            LayerCoverage(
                layer=DataLayer.L5_SOURCE_GRAPH.value, status=CoverageStatus.PRESENT
            ),
        ]
        return pack

    def test_l4_l5_coverage_demoted_at_build_depth(self) -> None:
        capped = project_build_source_pack_to_depth(self._pack(), "build")
        assert capped is not None
        rows = {row.layer: row.status for row in capped.manifest.coverage}
        assert rows[DataLayer.L3_BUILD.value] == CoverageStatus.PRESENT
        assert rows[DataLayer.L4_SOURCE_ABI.value] == CoverageStatus.NOT_COLLECTED
        assert rows[DataLayer.L5_SOURCE_GRAPH.value] == CoverageStatus.NOT_COLLECTED

    def test_coverage_untouched_at_source_depth(self) -> None:
        capped = project_build_source_pack_to_depth(self._pack(), "source")
        assert capped is not None
        rows = {row.layer: row.status for row in capped.manifest.coverage}
        assert rows[DataLayer.L4_SOURCE_ABI.value] == CoverageStatus.PRESENT
        assert rows[DataLayer.L5_SOURCE_GRAPH.value] == CoverageStatus.PRESENT


class TestOutOfBandPackCapping:
    """Pinned gap 3: an explicit --sources/--build-info pack must be capped too."""

    def _write_pack(self, root: Path, *, macro_value: str) -> None:
        from abicheck.buildsource import pack_io
        from abicheck.buildsource.build_evidence import (
            BuildEvidence,
            BuildOption,
            CompileUnit,
        )
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_abi import SourceAbiSurface, SourceEntity

        cu = CompileUnit(
            id="cu1",
            source="a.c",
            output="a.o",
            directory=".",
            target_id="t",
            compiler="gcc",
            argv=["gcc"],
        )
        be = BuildEvidence(
            compile_units=[cu],
            build_options=[
                BuildOption(
                    key="cxx_flag:fno-rtti",
                    value="on",
                    abi_relevant=True,
                    scope="global",
                    raw="-fno-rtti",
                )
            ],
        )
        macro = SourceEntity(
            id="macro:FOO",
            kind="macro",
            qualified_name="FOO",
            mangled_name="",
            signature_hash="",
            body_hash="",
            type_hash="",
            value=macro_value,
            api_relevant=True,
            visibility="public",
        )
        surface = SourceAbiSurface(reachable_macros=[macro])
        pack_io.write(BuildSourcePack(root=root, build_evidence=be, source_abi=surface))

    def _write_pair(self, tmp_path: Path) -> tuple[Path, Path, Path, Path]:
        old = AbiSnapshot(library="lib", version="1", from_headers=False)
        new = AbiSnapshot(library="lib", version="2", from_headers=False)
        old_path = tmp_path / "old.json"
        new_path = tmp_path / "new.json"
        save_snapshot(old, old_path)
        save_snapshot(new, new_path)
        old_pack = tmp_path / "old_pack"
        new_pack = tmp_path / "new_pack"
        self._write_pack(old_pack, macro_value="1")
        self._write_pack(new_pack, macro_value="2")
        return old_path, new_path, old_pack, new_pack

    def _invoke_with_pack(
        self,
        old_path: Path,
        new_path: Path,
        old_pack: Path,
        new_pack: Path,
        depth: str | None,
    ) -> tuple[int, str]:
        args = [
            "compare",
            str(old_path),
            str(new_path),
            "--sources",
            f"old={old_pack}",
            "--sources",
            f"new={new_pack}",
        ]
        if depth is not None:
            args += ["--depth", depth]
        result = CliRunner().invoke(main, args)
        return result.exit_code, result.stdout

    def test_binary_depth_sees_neither_l3_nor_l4_pack_evidence(
        self, tmp_path: Path
    ) -> None:
        old_path, new_path, old_pack, new_pack = self._write_pair(tmp_path)
        code, out = self._invoke_with_pack(
            old_path, new_path, old_pack, new_pack, "binary"
        )
        assert code == 0
        assert "fno-rtti" not in out.lower()
        assert "foo" not in out.lower()

    def test_build_depth_sees_l3_but_not_l4_pack_evidence(self, tmp_path: Path) -> None:
        old_path, new_path, old_pack, new_pack = self._write_pair(tmp_path)
        code, out = self._invoke_with_pack(
            old_path, new_path, old_pack, new_pack, "build"
        )
        # An L3 build-flag drift finding is RISK-category, never BREAKING/
        # API_BREAK on its own (ADR-028 D3), so this stays exit 0 -- the
        # assertion that matters here is that the L4 macro finding (which
        # *would* exit non-zero) is absent.
        assert code == 0
        assert "public_macro_value_changed" not in out.lower()

    def test_source_depth_sees_the_l4_pack_evidence(self, tmp_path: Path) -> None:
        old_path, new_path, old_pack, new_pack = self._write_pair(tmp_path)
        code, out = self._invoke_with_pack(
            old_path, new_path, old_pack, new_pack, "source"
        )
        assert code != 0
        assert "public_macro_value_changed" in out.lower()
