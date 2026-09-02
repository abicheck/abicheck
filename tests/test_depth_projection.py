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
   content was incorrectly retained.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck import checker
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
from abicheck.model.dwarf_facts import DwarfMetadata, EnumInfo, StructLayout
from abicheck.model.extraction_contract import ExtractionContract
from abicheck.model.source_graph import SourceGraphSummary
from abicheck.policy.depth_projection import project_snapshot_to_depth
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


class TestBinaryDepthWithDwarf:
    """DWARF-informed structural facts are an L0/L1 fact, kept at ``binary``."""

    def test_dwarf_visible_structural_break_still_detected(self) -> None:
        # `structs={"S": ...}` -- DWARF specifically observed *this* record by
        # name (`_dwarf_confirmed_names`'s real per-record evidence), not
        # merely `has_dwarf=True` on the whole snapshot; a struct that isn't
        # in this dict is a header-only fact even when its snapshot carries
        # unrelated real DWARF content elsewhere (Codex review, third round).
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
        assert result.verdict == checker.Verdict.BREAKING
        assert {c.kind.value for c in result.changes} == {"type_field_type_changed"}

    def test_header_only_fact_still_cleared(self) -> None:
        old, new = _headers_only_pair(dwarf=True)
        old_b = project_snapshot_to_depth(old, "binary")
        new_b = project_snapshot_to_depth(new, "binary")
        result = checker.compare(old_b, new_b)
        # The pair differs only in `constants` (a header-only fact) -- with
        # DWARF present, structural facts survive but the constant value
        # must still be cleared, exactly the same as the no-DWARF case.
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


class TestPerRecordDwarfConfirmation:
    """Pinned gap 6: DWARF confirmation is per-record, not whole-snapshot."""

    def _snap(self, *, field_type: str, member_value: int) -> AbiSnapshot:
        # `dwarf.has_dwarf=True` alone (the old, coarser gate) would keep both
        # `Confirmed` and `Unconfirmed` below; only `Confirmed`/`ConfirmedE`
        # are in `structs`/`enums`, so only they should survive.
        dwarf_meta = DwarfMetadata(
            has_dwarf=True,
            structs={
                "Confirmed": StructLayout(name="Confirmed", byte_size=8, fields=[])
            },
            enums={
                "ConfirmedE": EnumInfo(
                    name="ConfirmedE", underlying_byte_size=4, members={}
                )
            },
        )
        return AbiSnapshot(
            library="lib",
            version="1",
            from_headers=True,
            dwarf=dwarf_meta,
            types=[
                RecordType(
                    name="Confirmed",
                    kind="struct",
                    size_bits=64,
                    fields=[TypeField(name="x", type=field_type)],
                    origin=ScopeOrigin.PUBLIC_HEADER,
                ),
                RecordType(
                    name="Unconfirmed",
                    kind="struct",
                    size_bits=64,
                    fields=[TypeField(name="x", type=field_type)],
                    origin=ScopeOrigin.PUBLIC_HEADER,
                ),
            ],
            enums=[
                EnumType(
                    name="ConfirmedE",
                    members=[EnumMember(name="A", value=member_value)],
                    origin=ScopeOrigin.PUBLIC_HEADER,
                ),
                EnumType(
                    name="UnconfirmedE",
                    members=[EnumMember(name="A", value=member_value)],
                    origin=ScopeOrigin.PUBLIC_HEADER,
                ),
            ],
        )

    def test_only_dwarf_confirmed_records_survive(self) -> None:
        projected = project_snapshot_to_depth(
            self._snap(field_type="int", member_value=0), "binary"
        )
        assert [t.name for t in projected.types] == ["Confirmed"]
        assert [e.name for e in projected.enums] == ["ConfirmedE"]

    def test_unconfirmed_record_break_is_not_reported(self) -> None:
        old = self._snap(field_type="int", member_value=0)
        new = self._snap(field_type="double", member_value=1)
        # Only the *unconfirmed* declarations differ between old/new.
        old.types = [t for t in old.types if t.name == "Unconfirmed"]
        new.types = [t for t in new.types if t.name == "Unconfirmed"]
        old.enums = [e for e in old.enums if e.name == "UnconfirmedE"]
        new.enums = [e for e in new.enums if e.name == "UnconfirmedE"]
        old_b = project_snapshot_to_depth(old, "binary")
        new_b = project_snapshot_to_depth(new, "binary")
        result = checker.compare(old_b, new_b)
        assert result.verdict == checker.Verdict.NO_CHANGE

    def test_confirmed_record_break_is_still_reported(self) -> None:
        old = self._snap(field_type="int", member_value=0)
        new = self._snap(field_type="double", member_value=0)
        # Only the *confirmed* struct's field type differs.
        old.types = [t for t in old.types if t.name == "Confirmed"]
        new.types = [t for t in new.types if t.name == "Confirmed"]
        old.enums = []
        new.enums = []
        old_b = project_snapshot_to_depth(old, "binary")
        new_b = project_snapshot_to_depth(new, "binary")
        result = checker.compare(old_b, new_b)
        assert result.verdict == checker.Verdict.BREAKING
        assert {c.kind.value for c in result.changes} == {"type_field_type_changed"}


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
