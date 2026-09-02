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

Two real, review-caught gaps are pinned by name here so they don't recur:

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
from abicheck.model.dwarf_facts import DwarfMetadata
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
        dwarf_meta = DwarfMetadata(has_dwarf=True)
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
