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

"""Analysis-assurance axis: L5 source-graph completeness (degraded,
narrowed, unknown, and confirmed-zero-edge passes) and the family
coverage each side confirmed."""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck import checker
from tests._analysis_assurance_support import (
    header_pair,
)


class TestGraphCompleteness:
    """P1 review (finding 2): ``graph_completeness`` previously only ever
    looked at ``degraded_passes`` and defaulted to ``"complete"`` for every
    other state -- silently treating a narrowed-scope pass, absent pass
    coverage, and old/new graph asymmetry as full coverage."""

    def _pack_with_graph(self, tmp_path: Path, name: str, graph) -> object:
        from abicheck.buildsource.pack import BuildSourcePack

        return BuildSourcePack(root=tmp_path / name, source_graph=graph)

    def test_narrowed_pass_is_not_complete(self, tmp_path: Path) -> None:
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old, new = header_pair()
        graph = SourceGraphSummary(narrowed_passes={"call_graph": True})
        old.build_source = self._pack_with_graph(tmp_path, "old_pack", graph)
        new.build_source = self._pack_with_graph(
            tmp_path,
            "new_pack",
            SourceGraphSummary(narrowed_passes={"call_graph": True}),
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.graph_completeness == "narrowed"
        assert aa.status == "partial"
        assert any("narrowed" in n for n in aa.notes)

    def test_missing_pass_coverage_is_unknown_not_complete(
        self, tmp_path: Path
    ) -> None:
        """A graph with no extractor_passes/narrowed_passes recorded at all
        (e.g. a hand-built or pre-slice-2 graph) must not default to
        "complete" -- which parts of the project it covers is unknown."""
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old, new = header_pair()
        old.build_source = self._pack_with_graph(
            tmp_path, "old_pack", SourceGraphSummary()
        )
        new.build_source = self._pack_with_graph(
            tmp_path, "new_pack", SourceGraphSummary()
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.graph_completeness == "unknown"
        assert aa.status == "partial"

    def test_asymmetric_graph_presence_is_unknown_not_complete(
        self, tmp_path: Path
    ) -> None:
        """Only one side carries an L5 graph -- the comparison never
        examined the other side's implementation at all."""
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old, new = header_pair()
        old.build_source = self._pack_with_graph(
            tmp_path,
            "old_pack",
            SourceGraphSummary(extractor_passes={"call_graph": True}),
        )
        assert new.build_source is None
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.graph_completeness == "unknown"
        assert aa.status == "partial"
        assert any("only" in n or "unknown" in n for n in aa.notes)

    def test_full_confirmed_passes_on_both_sides_is_complete(
        self, tmp_path: Path
    ) -> None:
        """The positive case: both sides ran a confirmed full-project pass,
        clean -- graph_completeness must still read complete."""
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old, new = header_pair()
        old.build_source = self._pack_with_graph(
            tmp_path,
            "old_pack",
            SourceGraphSummary(extractor_passes={"call_graph": True}),
        )
        new.build_source = self._pack_with_graph(
            tmp_path,
            "new_pack",
            SourceGraphSummary(extractor_passes={"call_graph": True}),
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.graph_completeness == "complete"
        assert aa.status == "complete"

    def test_asymmetric_confirmed_family_coverage_is_not_complete(
        self, tmp_path: Path
    ) -> None:
        """P1 review, round 8: both sides can individually be "confirmed
        complete" on a nonempty extractor_passes mapping while covering
        DIFFERENT pass families entirely -- an old header-only graph
        confirmed on header_call_graph/header_type_graph vs. a new
        build-integrated graph confirmed on call_graph/type_graph. The
        cross-snapshot graph diff only ever compares edges within a shared
        family, so a comparison with no overlapping confirmed family must
        not read as "complete"."""
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old, new = header_pair()
        old.build_source = self._pack_with_graph(
            tmp_path,
            "old_pack",
            SourceGraphSummary(
                extractor_passes={
                    "header_call_graph": True,
                    "header_type_graph": True,
                }
            ),
        )
        new.build_source = self._pack_with_graph(
            tmp_path,
            "new_pack",
            SourceGraphSummary(
                extractor_passes={"call_graph": True, "type_graph": True}
            ),
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.graph_completeness != "complete"
        assert aa.graph_completeness == "unknown"
        assert aa.status == "partial"
        assert any("confirmed on only one side" in n for n in aa.notes)

    def test_overlapping_confirmed_family_on_same_name_is_still_complete(
        self, tmp_path: Path
    ) -> None:
        """Companion to the asymmetric-family test above: both sides
        confirmed complete on the SAME family name must still read
        "complete" -- the new overlap check must not regress the ordinary,
        matching-family case."""
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old, new = header_pair()
        old.build_source = self._pack_with_graph(
            tmp_path,
            "old_pack",
            SourceGraphSummary(
                extractor_passes={"call_graph": True, "type_graph": True}
            ),
        )
        new.build_source = self._pack_with_graph(
            tmp_path,
            "new_pack",
            SourceGraphSummary(
                extractor_passes={"call_graph": True, "type_graph": True}
            ),
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.graph_completeness == "complete"
        assert aa.status == "complete"

    def test_degraded_pass_still_reads_degraded_not_narrowed_or_unknown(
        self, tmp_path: Path
    ) -> None:
        """Regression guard: degraded_passes must keep taking priority over
        the new narrowed/unknown states, not be masked by them."""
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old, new = header_pair()
        old.build_source = self._pack_with_graph(
            tmp_path,
            "old_pack",
            SourceGraphSummary(degraded_passes={"call_graph": True}),
        )
        new.build_source = self._pack_with_graph(
            tmp_path,
            "new_pack",
            SourceGraphSummary(extractor_passes={"call_graph": True}),
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.graph_completeness == "degraded"
        assert aa.status == "partial"

    def test_partial_l5_extractor_record_is_not_complete(self, tmp_path: Path) -> None:
        """P1 review (finding: include L5 extractor failures in assurance).
        A manifest recording one successful L5 graph extractor
        (``call_graph:clang``) alongside a sibling that is ``partial``
        (``type_graph:clang``) must not read as complete just because every
        boolean extractor_passes/narrowed_passes/degraded_passes flag on the
        SourceGraphSummary itself happens to be clean."""
        from abicheck.buildsource.model import ExtractorRecord
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old, new = header_pair()
        old.build_source = BuildSourcePack(
            root=tmp_path / "old_pack",
            source_graph=SourceGraphSummary(extractor_passes={"call_graph": True}),
        )
        old.build_source.manifest.extractors = [
            ExtractorRecord(name="call_graph:clang", status="ok"),
            ExtractorRecord(
                name="type_graph:clang", status="partial", detail="0 type edges"
            ),
        ]
        new.build_source = BuildSourcePack(
            root=tmp_path / "new_pack",
            source_graph=SourceGraphSummary(extractor_passes={"call_graph": True}),
        )
        new.build_source.manifest.extractors = [
            ExtractorRecord(name="call_graph:clang", status="ok"),
            ExtractorRecord(name="type_graph:clang", status="ok"),
        ]
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.graph_completeness == "degraded"
        assert aa.status == "partial"
        assert any("type_graph:clang" in n and "partial" in n for n in aa.notes)

    def test_failed_l5_extractor_record_is_not_complete(self, tmp_path: Path) -> None:
        from abicheck.buildsource.model import ExtractorRecord
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old, new = header_pair()
        old.build_source = BuildSourcePack(
            root=tmp_path / "old_pack2",
            source_graph=SourceGraphSummary(extractor_passes={"call_graph": True}),
        )
        old.build_source.manifest.extractors = [
            ExtractorRecord(
                name="include_graph:clang", status="failed", detail="clang++ not found"
            ),
        ]
        new.build_source = BuildSourcePack(
            root=tmp_path / "new_pack2",
            source_graph=SourceGraphSummary(extractor_passes={"call_graph": True}),
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.graph_completeness == "degraded"
        assert aa.status == "partial"

    @pytest.mark.parametrize("flag_field", ["degraded_passes", "narrowed_passes"])
    def test_pass_flag_stored_false_is_not_a_shortfall(
        self, tmp_path: Path, flag_field: str
    ) -> None:
        """A degraded/narrowed entry recorded as ``False`` states that the
        pass did NOT degrade or narrow. It must not move a fully confirmed
        graph off ``complete``."""
        from abicheck.buildsource.source_graph import SourceGraphSummary

        def graph() -> SourceGraphSummary:
            return SourceGraphSummary(
                extractor_passes={"call_graph": True},
                **{flag_field: {"call_graph": False}},
            )

        old, new = header_pair()
        old.build_source = self._pack_with_graph(tmp_path, "old_pack", graph())
        new.build_source = self._pack_with_graph(tmp_path, "new_pack", graph())
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.graph_completeness == "complete", aa.notes
        assert aa.status == "complete", aa.notes

    def test_non_graph_extractor_record_is_not_an_l5_shortfall(
        self, tmp_path: Path
    ) -> None:
        """Only the L5 graph families feed graph completeness. A manifest
        record from any other extractor family, even one not ``ok``, must
        not degrade the source-graph axis."""
        from abicheck.buildsource.model import BuildSourceManifest, ExtractorRecord
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_graph import SourceGraphSummary

        def pack(name: str) -> BuildSourcePack:
            return BuildSourcePack(
                root=tmp_path / name,
                source_graph=SourceGraphSummary(extractor_passes={"call_graph": True}),
                manifest=BuildSourceManifest(
                    extractors=[
                        ExtractorRecord(
                            name="dwarf_advanced:libdwarf",
                            status="partial",
                            detail="not an L5 graph extractor",
                        ),
                    ],
                ),
            )

        old, new = header_pair()
        old.build_source = pack("old_pack")
        new.build_source = pack("new_pack")
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.graph_completeness == "complete", aa.notes
        assert not any("dwarf_advanced" in n for n in aa.notes), aa.notes


class TestConfirmedZeroEdgeGraphPassIsNotDegraded:
    """P2 review, Finding 3: a real producer
    (``buildsource/inline_graph_fold.py``'s ``fold_call_graph``/
    ``fold_type_graph``/... family) stamps its own ``ExtractorRecord.status``
    as ``"ok" if added else "partial"`` -- keyed on whether the pass found
    any edges, not on whether it examined everything it was asked to. A
    clean, full-project pass over a simple library with no calls/overrides/
    templates/etc. to discover legitimately records ``status="partial"``
    while *also* stamping ``SourceGraphSummary.extractor_passes[family] =
    True`` (confirmed full coverage) unconditionally on success. Treating
    every non-``"ok"`` record as a shortfall -- the fix this session
    previously landed for a *different*, real gap (an L5 extractor whose
    family the ``SourceGraphSummary`` booleans said nothing about at all) --
    regressed this legitimate case into a false ``"degraded"``/``"partial"``
    reading. Both directions are tested here so a future change cannot
    silently re-break either one.
    """

    def test_confirmed_full_pass_with_zero_edges_reads_complete(
        self, tmp_path: Path
    ) -> None:
        """(b) A confirmed, zero-edge, fully-executed pass -- the exact
        shape ``inline_graph_fold.py`` really produces for an edge-free
        project -- must read as complete, not degraded."""
        from abicheck.buildsource.model import ExtractorRecord
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old, new = header_pair()
        for side, root in (("old", "confirmed_old"), ("new", "confirmed_new")):
            pack = BuildSourcePack(
                root=tmp_path / root,
                source_graph=SourceGraphSummary(extractor_passes={"call_graph": True}),
            )
            pack.manifest.extractors = [
                ExtractorRecord(
                    name="call_graph:clang",
                    status="partial",
                    detail="0 call edges from 3 compile units",
                ),
            ]
            setattr(old if side == "old" else new, "build_source", pack)
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.graph_completeness == "complete", aa.notes
        assert aa.status == "complete"
        assert not any("call_graph:clang" in n for n in aa.notes)

    def test_confirmed_narrowed_pass_with_partial_status_is_narrowed(
        self, tmp_path: Path
    ) -> None:
        """A confirmed *narrowed* (not full-project) zero-edge pass must
        still read as ``"narrowed"`` -- not silently promoted to
        ``"complete"``, and not double-counted into ``"degraded"`` either."""
        from abicheck.buildsource.model import ExtractorRecord
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old, new = header_pair()
        for side, root in (("old", "narrowed_old"), ("new", "narrowed_new")):
            pack = BuildSourcePack(
                root=tmp_path / root,
                source_graph=SourceGraphSummary(narrowed_passes={"call_graph": True}),
            )
            pack.manifest.extractors = [
                ExtractorRecord(
                    name="call_graph:clang",
                    status="partial",
                    detail="0 call edges from 1 compile unit (narrowed)",
                ),
            ]
            setattr(old if side == "old" else new, "build_source", pack)
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.graph_completeness == "narrowed"
        assert aa.status == "partial"
        assert not any("call_graph:clang" in n for n in aa.notes)

    def test_unconfirmed_partial_record_still_reads_degraded(
        self, tmp_path: Path
    ) -> None:
        """(a) The P1 finding this session's own prior fix addressed must
        stay fixed: a partial/failed L5 extractor record whose family is
        NOT separately confirmed by extractor_passes/narrowed_passes (a
        genuine shortfall -- e.g. a per-TU crash that stopped short of
        setting either) must still fold into degraded/partial, never
        silently pass as complete."""
        from abicheck.buildsource.model import ExtractorRecord
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old, new = header_pair()
        old.build_source = BuildSourcePack(
            root=tmp_path / "shortfall_old",
            source_graph=SourceGraphSummary(extractor_passes={"call_graph": True}),
        )
        old.build_source.manifest.extractors = [
            ExtractorRecord(name="call_graph:clang", status="ok"),
            ExtractorRecord(
                name="type_graph:clang",
                status="partial",
                detail="crashed on 2/10 TUs",
            ),
        ]
        new.build_source = BuildSourcePack(
            root=tmp_path / "shortfall_new",
            source_graph=SourceGraphSummary(extractor_passes={"call_graph": True}),
        )
        new.build_source.manifest.extractors = [
            ExtractorRecord(name="call_graph:clang", status="ok"),
            ExtractorRecord(name="type_graph:clang", status="ok"),
        ]
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.graph_completeness == "degraded"
        assert aa.status == "partial"
        assert any("type_graph:clang" in n and "partial" in n for n in aa.notes)
