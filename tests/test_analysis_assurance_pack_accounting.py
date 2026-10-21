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

"""Analysis-assurance axis: build-pack accounting (target scope,
out-of-band packs, manifest-level layer incompleteness, and export
accounting on both sides)."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from abicheck import checker
from abicheck.cli import main
from abicheck.policy.analysis_assurance import (
    AnalysisAssurance,
    analysis_assurance_exit_contribution,
    compute_analysis_assurance,
)
from tests._analysis_assurance_support import (
    assurance_config_args,
    header_pair,
    write_snapshot_pair,
)


class TestTargetAccounting:
    """P1 review, Finding 2: P0.2's Bazel root-target resolution
    (``BuildEvidence.target_scope``) has landed, but ``target_accounting``
    was left permanently empty pending it -- hiding a genuine partial
    resolution (e.g. two requested Bazel roots with only one resolved) with
    no predicate downgrading the overall status for the gap."""

    def test_fully_resolved_target_scope_is_populated_not_partial_for_it(
        self,
    ) -> None:
        from abicheck.buildsource.build_evidence import BuildEvidence, TargetScope
        from abicheck.buildsource.pack import BuildSourcePack

        old, new = header_pair()
        new.build_source = BuildSourcePack(
            root=Path("/tmp/nonexistent-pack-targets-full"),
            build_evidence=BuildEvidence(
                target_scope=TargetScope(
                    requested=["//foo:bar", "//foo:baz"],
                    resolved=["//foo:bar", "//foo:baz"],
                    transitive_count=12,
                )
            ),
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.target_accounting.requested == ("//foo:bar", "//foo:baz")
        assert aa.target_accounting.resolved == ("//foo:bar", "//foo:baz")
        assert aa.target_accounting.transitive_count == 12

    def test_two_requested_one_resolved_is_partial_not_complete(self) -> None:
        """The exact shape named in the review finding: two requested Bazel
        roots, only one resolved (a typo'd target label, say)."""
        from abicheck.buildsource.build_evidence import BuildEvidence, TargetScope
        from abicheck.buildsource.pack import BuildSourcePack

        old, new = header_pair()
        new.build_source = BuildSourcePack(
            root=Path("/tmp/nonexistent-pack-targets-partial"),
            build_evidence=BuildEvidence(
                target_scope=TargetScope(
                    requested=["//foo:bar", "//foo:typo"],
                    resolved=["//foo:bar"],
                )
            ),
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.target_accounting.requested == ("//foo:bar", "//foo:typo")
        assert aa.target_accounting.resolved == ("//foo:bar",)
        assert aa.status != "complete"
        assert any("did not all resolve" in n for n in aa.notes)

    def test_no_target_scope_requested_stays_none_not_empty(self) -> None:
        """When neither side requested root-target scoping at all,
        target_accounting must stay the documented "not requested" None
        state, not an empty tuple."""
        old, new = header_pair()
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.target_accounting.requested is None
        assert aa.target_accounting.resolved is None

    def test_require_complete_analysis_exits_nonzero_for_partial_targets(
        self,
    ) -> None:
        from abicheck.buildsource.build_evidence import BuildEvidence, TargetScope
        from abicheck.buildsource.pack import BuildSourcePack

        old, new = header_pair()
        new.build_source = BuildSourcePack(
            root=Path("/tmp/nonexistent-pack-targets-cli"),
            build_evidence=BuildEvidence(
                target_scope=TargetScope(
                    requested=["//foo:bar", "//foo:typo"],
                    resolved=["//foo:bar"],
                )
            ),
        )
        result = checker.compare(old, new)
        assert analysis_assurance_exit_contribution(result, require_complete=True) == 1
        assert analysis_assurance_exit_contribution(result, require_complete=False) == 0


class TestAnalysisAssuranceOutOfBandPack:
    """P1 review (finding 1): ``--build-info``/``--sources`` point ``compare``
    at an out-of-band pack directory that ``_resolve_side_pack`` uses for the
    run's real findings and coverage *without* ever attaching it back onto
    ``old``/``new``. Before the fix, ``analysis_assurance`` was computed from
    the bare snapshots alone (which carry no embedded ``build_source`` here),
    so a genuinely partial/failed out-of-band pack was invisible to it --
    ``status`` could read ``"complete"`` and ``--require-complete-analysis``
    would exit 0 despite the real evidence being incomplete. Regression
    coverage mirrors ``TestEvidenceDepthOutOfBandPack`` in
    ``tests/test_reporter_side_facts.py``, the existing test for the identical class of
    gap on the ``old_evidence_depth``/``new_evidence_depth`` JSON fields.
    """

    def _write_partial_pack(self, tmp_path: Path, name: str) -> Path:
        """A real, on-disk out-of-band pack whose L4 source-ABI surface is
        genuinely partial: 10 compile units selected, only 3 parsed."""
        from abicheck.buildsource import BuildSourcePack, pack_io
        from abicheck.buildsource.source_abi import SourceAbiSurface

        pack_dir = tmp_path / name
        pack = BuildSourcePack(
            root=pack_dir,
            source_abi=SourceAbiSurface(
                coverage={
                    "compile_units_selected": 10,
                    "compile_units_parsed": 3,
                },
            ),
        )
        pack_io.write(pack)
        return pack_dir

    def test_out_of_band_pack_partial_evidence_is_not_complete(
        self, tmp_path: Path
    ) -> None:
        old, new = header_pair()
        # Neither snapshot carries an embedded build_source -- the only
        # evidence of incompleteness is the out-of-band pack directory.
        assert old.build_source is None
        assert new.build_source is None
        old_p, new_p = write_snapshot_pair(tmp_path, old, new)
        pack_dir = self._write_partial_pack(tmp_path, "old_pack")

        res = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--build-info",
                "old=" + str(pack_dir),
                "-o",
                "json=-",
            ],
        )
        assert res.exit_code in (0, 1, 2, 4), res.output
        payload = json.loads(res.output[res.output.index("{") :])
        aa = payload["analysis_assurance"]
        # Finding 1's actual claim: the *out-of-band* pack's incompleteness
        # (7 of 10 compile units never parsed) must be visible here, even
        # though it was never attached to old.build_source/new.build_source.
        assert aa["status"] != "complete", aa
        assert aa["translation_units"]["failed"] == 7, aa
        assert aa["translation_units"]["selected"] == 10, aa

    def test_require_complete_analysis_exits_nonzero_for_out_of_band_partial_pack(
        self, tmp_path: Path
    ) -> None:
        old, new = header_pair()
        old_p, new_p = write_snapshot_pair(tmp_path, old, new)
        pack_dir = self._write_partial_pack(tmp_path, "old_pack")

        res = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--build-info",
                "old=" + str(pack_dir),
                *assurance_config_args(tmp_path),
            ],
        )
        # This is the whole point of --require-complete-analysis: a partial
        # out-of-band pack must not be able to exit 0.
        assert res.exit_code != 0, res.output

    def test_out_of_band_pack_beats_absent_embedded_snapshot_directly(self) -> None:
        """Direct unit-level check of the recomputation itself (bypassing the
        CLI): compute_analysis_assurance must reflect the pack passed via
        old_pack/new_pack, not whatever old.build_source/new.build_source
        happen to carry (nothing, here)."""
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_abi import SourceAbiSurface

        old, new = header_pair()
        assert old.build_source is None
        assert new.build_source is None
        result = checker.compare(old, new)
        # Sanity: with no pack at all, the bare rollup reads complete.
        assert result.analysis_assurance.status == "complete"

        pack = BuildSourcePack(
            root=Path("/tmp/nonexistent-out-of-band-pack"),
            source_abi=SourceAbiSurface(
                coverage={
                    "compile_units_selected": 10,
                    "compile_units_parsed": 3,
                },
            ),
        )
        aa = compute_analysis_assurance(result, old, new, old_pack=pack)
        assert aa.status != "complete"
        assert aa.translation_units.failed == 7


class TestAnalysisAssurancePartialManifestLayer:
    """New review finding (P1): both sides carry an L4 pack whose extraction
    failed before producing any TU counts -- e.g.
    ``inline._run_inline_source_abi()``'s clang-unavailable path, which
    returns a bare ``SourceAbiSurface()`` (present, but with an empty
    ``coverage`` dict) and records the pack's own manifest L4 coverage row as
    ``partial`` (``build_inline_coverage``'s ``any_entities`` check). Before
    this fix, ``compute_analysis_assurance`` never looked at the manifest at
    all: both sides read as fact-set ``comparable`` (no
    ``fact_set_inconsistent`` flag -- there's no coverage dict to carry one),
    ``translation_units.failed`` stayed ``None`` (nothing to subtract, since
    ``selected``/``parsed`` were never set rather than zero), and status fell
    through to ``"complete"`` -- letting ``--require-complete-analysis`` exit
    0 even though L4 extraction failed entirely on both sides.
    """

    def _failed_l4_pack(self, tmp_path: Path, name: str):
        from abicheck.buildsource.model import (
            BuildSourceManifest,
            CoverageStatus,
            DataLayer,
            LayerCoverage,
        )
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_abi import SourceAbiSurface

        manifest = BuildSourceManifest(
            coverage=[
                LayerCoverage(
                    layer=DataLayer.L4_SOURCE_ABI.value,
                    status=CoverageStatus.PARTIAL,
                    detail="clang not found in PATH; source-only checks disabled",
                ),
            ],
        )
        return BuildSourcePack(
            root=tmp_path / name,
            manifest=manifest,
            # Mirrors inline._run_inline_source_abi()'s own clang-unavailable
            # return value verbatim: present, but with no real facts and no
            # TU accounting at all.
            source_abi=SourceAbiSurface(),
        )

    def test_both_sides_partial_l4_manifest_is_not_complete(
        self, tmp_path: Path
    ) -> None:
        old, new = header_pair()
        old.build_source = self._failed_l4_pack(tmp_path, "old_pack")
        new.build_source = self._failed_l4_pack(tmp_path, "new_pack")

        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        # The manifest's own partial L4 row must be visible in the rollup...
        assert aa.status != "complete", aa
        assert any("partial" in n for n in aa.notes), aa.notes

    def test_require_complete_analysis_exits_nonzero_for_partial_l4_manifest(
        self, tmp_path: Path
    ) -> None:
        old, new = header_pair()
        old.build_source = self._failed_l4_pack(tmp_path, "old_pack")
        new.build_source = self._failed_l4_pack(tmp_path, "new_pack")
        old_p, new_p = write_snapshot_pair(tmp_path, old, new)

        res = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                *assurance_config_args(tmp_path),
            ],
        )
        assert res.exit_code != 0, res.output

    def test_l4_present_without_any_tu_accounting_is_flagged_directly(self) -> None:
        """Unit-level check of the second, independent signal this fix adds:
        an L4 surface present on a side with no compile_units_selected/
        compile_units_parsed keys at all (not zero -- simply absent), even
        with no manifest attached, must not read as complete."""
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_abi import SourceAbiSurface

        old, new = header_pair()
        pack = BuildSourcePack(
            root=Path("/tmp/nonexistent-empty-l4-pack"),
            source_abi=SourceAbiSurface(),
        )
        result = checker.compare(old, new)
        aa = compute_analysis_assurance(result, old, new, old_pack=pack, new_pack=pack)
        assert aa.status != "complete"
        assert any("no translation-unit accounting" in n for n in aa.notes), aa.notes


class TestExportAccountingBothSides:
    """Round-7 review, Finding 1 (``discussion_r3787513758``): the previous
    ``_export_accounting`` picked ONE side's accounting -- new first,
    falling back to old only when new carried no L4 surface at all -- so a
    comparison where BOTH sides link an L4 surface, but only the OLD side
    has unmatched exports, silently returned the NEW side's clean
    accounting and never examined the old side. ``export_accounting.
    unaccounted`` therefore read 0 and, with no other partial signal
    tripping, ``status`` could read ``"complete"`` under
    ``--require-complete-analysis`` despite genuinely incomplete baseline
    linking. Fixed by summing both sides' counts additively, mirroring
    ``_translation_units()``'s own both-sides summation.
    """

    def _pack_with_surface(
        self, tmp_path: Path, name: str, *, unaccounted: tuple[str, ...]
    ):
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_abi import SourceAbiSurface

        surface = SourceAbiSurface(
            roots={
                "exported_symbols": ["a", "b", "c"],
                "public_header_declarations": [],
                "forced_public": [],
            },
            unmatched={
                "symbols_without_decl": list(unaccounted),
                "decls_without_symbol": [],
            },
            # Set so this fixture isolates the export-accounting rollup --
            # without this, an empty ``coverage`` dict would also trip the
            # separate ``l4_present_without_accounting`` signal, confounding
            # what actually made ``status`` non-complete.
            coverage={"compile_units_selected": 3, "compile_units_parsed": 3},
        )
        return BuildSourcePack(root=tmp_path / name, source_abi=surface)

    def test_unaccounted_export_on_old_side_only_is_not_shadowed_by_new(
        self, tmp_path: Path
    ) -> None:
        old, new = header_pair()
        old_pack = self._pack_with_surface(tmp_path, "old_pack", unaccounted=("a",))
        new_pack = self._pack_with_surface(tmp_path, "new_pack", unaccounted=())

        result = checker.compare(old, new)
        aa = compute_analysis_assurance(
            result, old, new, old_pack=old_pack, new_pack=new_pack
        )
        # The old side's single unaccounted export must be visible in the
        # combined rollup -- not silently dropped because the new side (read
        # first in source order) happened to be clean.
        assert aa.export_accounting.unaccounted == 1
        assert aa.status != "complete"
        assert any("unaccounted" in n for n in aa.notes), aa.notes

    def test_both_sides_clean_stays_zero_unaccounted(self, tmp_path: Path) -> None:
        old, new = header_pair()
        old_pack = self._pack_with_surface(tmp_path, "old_pack", unaccounted=())
        new_pack = self._pack_with_surface(tmp_path, "new_pack", unaccounted=())

        result = checker.compare(old, new)
        aa = compute_analysis_assurance(
            result, old, new, old_pack=old_pack, new_pack=new_pack
        )
        assert aa.export_accounting.unaccounted == 0
        # Both sides contribute total exports (3 + 3 = 6), same additive
        # rollup as translation-unit accounting.
        assert aa.export_accounting.total == 6

    def test_require_complete_analysis_exits_nonzero_for_old_side_unaccounted(
        self, tmp_path: Path
    ) -> None:
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_abi import SourceAbiSurface

        old, new = header_pair()
        old.build_source = BuildSourcePack(
            root=tmp_path / "old_pack",
            source_abi=SourceAbiSurface(
                roots={
                    "exported_symbols": ["a"],
                    "public_header_declarations": [],
                    "forced_public": [],
                },
                unmatched={
                    "symbols_without_decl": ["a"],
                    "decls_without_symbol": [],
                },
            ),
        )
        new.build_source = BuildSourcePack(
            root=tmp_path / "new_pack",
            source_abi=SourceAbiSurface(
                roots={
                    "exported_symbols": ["a"],
                    "public_header_declarations": [],
                    "forced_public": [],
                },
            ),
        )
        old_p, new_p = write_snapshot_pair(tmp_path, old, new)
        res = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                *assurance_config_args(tmp_path),
            ],
        )
        assert res.exit_code != 0, res.output
