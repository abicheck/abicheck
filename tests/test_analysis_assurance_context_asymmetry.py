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

"""Analysis-assurance axis: one-sided evidence on either side of a
comparison (header, DWARF, binary, L3 build context, and the DWARF
channel split) must read as asymmetric, never as clean."""

from __future__ import annotations

from pathlib import Path

from abicheck import checker
from abicheck.model import AbiSnapshot
from abicheck.policy.analysis_assurance import (
    AnalysisAssurance,
    analysis_assurance_exit_contribution,
    compute_analysis_assurance,
)
from tests._analysis_assurance_support import (
    assurance_config_args,
    compare_cli,
    fn,
    header_pair,
)


class TestHeaderContextAsymmetry:
    """P1 review (finding: one-sided header evidence): ``header_context_status``
    previously used ``or`` to decide header evidence was "present" -- so a
    header-snapshot-vs-ELF-only comparison read as fully evaluated (no drift
    finding to raise, since the ELF-only side has no headers to drift
    against), even though only half the comparison actually has header/API
    evidence. Must report the asymmetry explicitly and never read
    ``status="complete"`` for it."""

    def _asymmetric_pair(self) -> tuple[AbiSnapshot, AbiSnapshot]:
        fns = [fn("pub_a", "_Z5pub_av")]
        old = AbiSnapshot(
            version="1.0", library="libfoo.so.1", functions=fns, from_headers=True
        )
        new = AbiSnapshot(
            version="2.0",
            library="libfoo.so.1",
            functions=fns,
            elf_only_mode=True,
        )
        return old, new

    def test_one_sided_header_evidence_is_reported_as_asymmetric(self) -> None:
        old, new = self._asymmetric_pair()
        result = checker.compare(old, new, scope_to_public_surface=False)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.header_context_status == "asymmetric"
        assert aa.status != "complete"
        assert any("asymmetric" in n for n in aa.notes)

    def test_require_complete_analysis_exits_nonzero_for_one_sided_headers(
        self, tmp_path: Path
    ) -> None:
        old, new = self._asymmetric_pair()
        res = compare_cli(
            tmp_path,
            (old, new),
            "--require-complete-analysis",
            scoped=False,
        )
        assert res.exception is None or isinstance(res.exception, SystemExit), (
            res.output
        )
        assert res.exit_code != 0, res.output


class TestDwarfContextAsymmetry:
    """P1 review, Finding 1: ``confidence._detect_evidence_tiers()`` combines
    both sides' DWARF availability with OR semantics when promoting the
    aggregate ``result.evidence_tier`` to ``DWARF_AWARE``, so a comparison
    where only one side carries usable DWARF still read as DWARF-aware
    overall -- with no per-side check anywhere in this rollup, that silently
    made ``nothing_requested`` false without any partial predicate tripping,
    so the run fell through to ``status="complete"`` even though the real
    DWARF-based layout detectors (``diff_platform.py``,
    ``dwarf_advanced.diff_advanced_dwarf``) explicitly skip their own
    comparison whenever either side lacks DWARF -- struct/enum layout
    changes on the DWARF-lacking side could be silently missed."""

    def _dwarf_asymmetric_pair(self) -> tuple[AbiSnapshot, AbiSnapshot]:
        from abicheck.dwarf_metadata import DwarfMetadata

        fns = [fn("pub_a", "_Z5pub_av")]
        old = AbiSnapshot(
            version="1.0",
            library="libfoo.so.1",
            functions=fns,
            dwarf=DwarfMetadata(has_dwarf=True),
        )
        new = AbiSnapshot(
            version="2.0",
            library="libfoo.so.1",
            functions=fns,
            dwarf=DwarfMetadata(has_dwarf=False),
        )
        return old, new

    def test_one_sided_dwarf_is_reported_as_asymmetric(self) -> None:
        old, new = self._dwarf_asymmetric_pair()
        result = checker.compare(old, new, scope_to_public_surface=False)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.dwarf_context_status == "asymmetric"
        assert aa.status != "complete"
        assert any("DWARF" in n and "asymmetric" in n for n in aa.notes)

    def test_symmetric_dwarf_on_both_sides_is_clean(self) -> None:
        from abicheck.dwarf_metadata import DwarfMetadata

        fns = [fn("pub_a", "_Z5pub_av")]
        old = AbiSnapshot(
            version="1.0",
            library="libfoo.so.1",
            functions=fns,
            dwarf=DwarfMetadata(has_dwarf=True),
        )
        new = AbiSnapshot(
            version="2.0",
            library="libfoo.so.1",
            functions=fns,
            dwarf=DwarfMetadata(has_dwarf=True),
        )
        result = checker.compare(old, new, scope_to_public_surface=False)
        aa = result.analysis_assurance
        assert aa.dwarf_context_status == "clean"

    def test_require_complete_analysis_exits_nonzero_for_one_sided_dwarf(
        self, tmp_path: Path
    ) -> None:
        old, new = self._dwarf_asymmetric_pair()
        res = compare_cli(
            tmp_path,
            (old, new),
            "--require-complete-analysis",
            scoped=False,
        )
        assert res.exception is None or isinstance(res.exception, SystemExit), (
            res.output
        )
        assert res.exit_code != 0, res.output


class TestL0ContextAsymmetry:
    """Self-audit finding (P0.4 review): the same one-sided-evidence pattern
    already fixed for L1 (DWARF)/L2 (headers)/L3 (build evidence) also
    applies to L0 -- a comparison where only one side carries a real binary
    export table (the other a synthetic/snapshot-only input,
    ``cli_scan_helpers._intrinsic_coverage``'s own documented "no binary
    export table (snapshot-only input)" state) previously fell through to
    ``status="complete"`` with nothing checking L0 presence by side."""

    def _asymmetric_pair(self) -> tuple[AbiSnapshot, AbiSnapshot]:
        from abicheck.elf_metadata import ElfMetadata

        fns = [fn("pub_a", "_Z5pub_av")]
        old = AbiSnapshot(
            version="1.0",
            library="libfoo.so.1",
            functions=fns,
            elf=ElfMetadata(soname="libfoo.so.1"),
        )
        new = AbiSnapshot(
            version="2.0",
            library="libfoo.so.1",
            functions=fns,
            elf=None,
        )
        return old, new

    def test_one_sided_binary_evidence_is_reported_as_asymmetric(self) -> None:
        old, new = self._asymmetric_pair()
        result = checker.compare(old, new, scope_to_public_surface=False)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.l0_context_status == "asymmetric"
        assert aa.status != "complete"
        assert any("L0" in n and "asymmetric" in n for n in aa.notes)

    def test_symmetric_binary_presence_on_both_sides_is_clean(self) -> None:
        from abicheck.elf_metadata import ElfMetadata

        fns = [fn("pub_a", "_Z5pub_av")]
        old = AbiSnapshot(
            version="1.0",
            library="libfoo.so.1",
            functions=fns,
            elf=ElfMetadata(soname="libfoo.so.1"),
        )
        new = AbiSnapshot(
            version="2.0",
            library="libfoo.so.1",
            functions=fns,
            elf=ElfMetadata(soname="libfoo.so.1"),
        )
        result = checker.compare(old, new, scope_to_public_surface=False)
        aa = result.analysis_assurance
        assert aa.l0_context_status == "clean"

    def test_symmetric_absence_is_not_evaluated_not_asymmetric(self) -> None:
        """Neither side carrying a binary (a pure header/source-only
        comparison) is a legitimate, symmetric shape, not an asymmetry."""
        old, new = header_pair()
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.l0_context_status == "not_evaluated"

    def test_require_complete_analysis_exits_nonzero_for_one_sided_binary(
        self, tmp_path: Path
    ) -> None:
        old, new = self._asymmetric_pair()
        res = compare_cli(
            tmp_path,
            (old, new),
            "--require-complete-analysis",
            scoped=False,
        )
        assert res.exception is None or isinstance(res.exception, SystemExit), (
            res.output
        )
        assert res.exit_code != 0, res.output


class TestL3ContextAsymmetry:
    """P1 review, Finding 3: when the baseline carries L3 ``BuildEvidence``
    but the target has none, ``prepare_embedded_build_source()`` already
    emits a prose-only ``layer_coverage_asymmetric`` finding -- but nothing
    in this rollup's own predicates examined L3 presence by side, so an
    otherwise-matching comparison fell through to ``status="complete"``."""

    def _asymmetric_pair(self) -> tuple[AbiSnapshot, AbiSnapshot]:
        from abicheck.buildsource.build_evidence import BuildEvidence
        from abicheck.buildsource.pack import BuildSourcePack

        old, new = header_pair()
        old.build_source = BuildSourcePack(
            root=Path("/tmp/nonexistent-pack-l3-old"),
            build_evidence=BuildEvidence(),
        )
        return old, new

    def test_one_sided_l3_evidence_is_reported_as_asymmetric(self) -> None:
        old, new = self._asymmetric_pair()
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.l3_context_status == "asymmetric"
        assert aa.status != "complete"
        assert any("L3" in n and "asymmetric" in n for n in aa.notes)

    def test_symmetric_l3_evidence_on_both_sides_is_clean(self) -> None:
        from abicheck.buildsource.build_evidence import BuildEvidence
        from abicheck.buildsource.pack import BuildSourcePack

        old, new = header_pair()
        old.build_source = BuildSourcePack(
            root=Path("/tmp/nonexistent-pack-l3-both-old"),
            build_evidence=BuildEvidence(),
        )
        new.build_source = BuildSourcePack(
            root=Path("/tmp/nonexistent-pack-l3-both-new"),
            build_evidence=BuildEvidence(),
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.l3_context_status == "clean"

    def test_require_complete_analysis_exits_nonzero_for_one_sided_l3(
        self,
    ) -> None:
        """Function-level check (mirrors this file's other
        analysis_assurance_exit_contribution-level checks) rather than a
        full CLI round-trip, since ``build_source`` is attached directly to
        the in-memory snapshot here (not via a CLI flag)."""
        old, new = self._asymmetric_pair()
        result = checker.compare(old, new)
        assert analysis_assurance_exit_contribution(result, require_complete=True) == 1
        assert analysis_assurance_exit_contribution(result, require_complete=False) == 0


class TestDwarfChannelAsymmetry:
    """Round-7 review, Finding 2 (``discussion_r3787513766``): the previous
    ``_dwarf_context_status`` OR'd ``dwarf.has_dwarf``/``dwarf_advanced.
    has_dwarf`` into ONE combined per-side boolean before comparing the two
    sides -- so an old snapshot with only basic ``dwarf`` and a new
    snapshot with only ``dwarf_advanced`` both read as "this side has SOME
    dwarf evidence", and the combined booleans compared equal (clean), even
    though ``diff_platform.py``'s struct/enum layout diff (basic ``dwarf``
    only) and ``dwarf_advanced.diff_advanced_dwarf`` (advanced only) each
    independently skip their own comparison whenever EITHER side lacks
    THEIR channel specifically. Fixed by checking each channel
    independently.
    """

    def _cross_channel_pair(self) -> tuple[AbiSnapshot, AbiSnapshot]:
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata
        from abicheck.dwarf_metadata import DwarfMetadata

        fns = [fn("pub_a", "_Z5pub_av")]
        old = AbiSnapshot(
            version="1.0",
            library="libfoo.so.1",
            functions=fns,
            dwarf=DwarfMetadata(has_dwarf=True),
            dwarf_advanced=AdvancedDwarfMetadata(has_dwarf=False),
        )
        new = AbiSnapshot(
            version="2.0",
            library="libfoo.so.1",
            functions=fns,
            dwarf=DwarfMetadata(has_dwarf=False),
            dwarf_advanced=AdvancedDwarfMetadata(has_dwarf=True),
        )
        return old, new

    def test_cross_channel_dwarf_is_reported_as_asymmetric(self) -> None:
        old, new = self._cross_channel_pair()
        result = checker.compare(old, new, scope_to_public_surface=False)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.dwarf_context_status == "asymmetric"
        assert aa.status != "complete"
        # Both channels must be named -- the old side lacks basic dwarf, the
        # new side lacks dwarf_advanced, and both are real, independent gaps.
        assert any("basic dwarf channel" in n for n in aa.notes), aa.notes
        assert any("dwarf_advanced channel" in n for n in aa.notes), aa.notes

    def test_both_channels_flagged_independently(self) -> None:
        old, new = self._cross_channel_pair()
        result = checker.compare(old, new, scope_to_public_surface=False)
        aa = compute_analysis_assurance(result, old, new)
        assert aa.dwarf_context_status == "asymmetric"
        channel_notes = [n for n in aa.notes if "DWARF context asymmetric" in n]
        assert len(channel_notes) == 2, aa.notes

    def test_matching_single_channel_on_both_sides_is_clean(self) -> None:
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata
        from abicheck.dwarf_metadata import DwarfMetadata

        fns = [fn("pub_a", "_Z5pub_av")]
        old = AbiSnapshot(
            version="1.0",
            library="libfoo.so.1",
            functions=fns,
            dwarf=DwarfMetadata(has_dwarf=False),
            dwarf_advanced=AdvancedDwarfMetadata(has_dwarf=True),
        )
        new = AbiSnapshot(
            version="2.0",
            library="libfoo.so.1",
            functions=fns,
            dwarf=DwarfMetadata(has_dwarf=False),
            dwarf_advanced=AdvancedDwarfMetadata(has_dwarf=True),
        )
        result = checker.compare(old, new, scope_to_public_surface=False)
        aa = result.analysis_assurance
        assert aa.dwarf_context_status == "clean"

    def test_require_complete_analysis_exits_nonzero_for_cross_channel_dwarf(
        self, tmp_path: Path
    ) -> None:
        old, new = self._cross_channel_pair()
        res = compare_cli(
            tmp_path,
            (old, new),
            *assurance_config_args(tmp_path, scoped=False),
        )
        assert res.exception is None or isinstance(res.exception, SystemExit), (
            res.output
        )
        assert res.exit_code != 0, res.output
