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

"""P1 review finding, fresh evidence on the already-resolved CFI-completeness
fix (split out rather than added to ``test_dwarf_coverage_gaps.py``, which
is already at its AI-readiness file-size no-growth debt baseline -- mirrors
the ``_extra``-style sibling pattern e.g. ``test_ctf_metadata_evidence.py``
uses for the identical reason).

Finding: ``_parse_frame_registers``'s own per-FDE ``except`` correctly
downgrades ``complete`` to ``False`` when accessing the FDE itself raises,
but ``_extract_cfa_reg_from_fde``/``_extract_callee_saved_regs`` each catch
and swallow their *own* decode failures internally, returning a plain
``None`` -- indistinguishable from "this FDE legitimately carries no CFA/
saved-register data." That left the outer loop's own ``except`` unreachable
for this failure shape, so a genuinely failed decode was reported as a
complete, ``parsed`` pass. Fixed by giving both helpers an opt-in
``decode_failed`` out-parameter (mirrors the ``truncated`` out-parameter
this PR's BTF/CTF fixes already use for the analogous problem), which
``_parse_frame_registers`` now reads to downgrade ``complete``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from abicheck.dwarf_advanced import AdvancedDwarfMetadata, _parse_frame_registers


def _fde_with_symbol(mock_dwarf, mock_elf, decode_error) -> None:
    """Wire up an FDE at a known exported address whose get_decoded()
    raises. Shared by both bug-class regression tests below."""
    dyn = MagicMock()
    sym = MagicMock()
    sym.name = "exported_fn"
    sym.entry.st_value = 0x1000
    sym.entry.st_info.bind = "STB_GLOBAL"
    dyn.iter_symbols.return_value = [sym]
    mock_elf.get_section_by_name.side_effect = lambda name: (
        dyn if name == ".dynsym" else None
    )

    fde = MagicMock()
    fde.__class__ = type("FDE", (), {})
    fde.__class__.__name__ = "FDE"
    fde.__getitem__ = MagicMock(return_value=0x1000)
    fde.get_decoded.side_effect = decode_error
    mock_dwarf.EH_CFI_entries.return_value = [fde]


class TestHelperLevelDecodeFailurePropagates:
    def test_shared_get_decoded_failure_is_not_reported_complete(self) -> None:
        """Covers both helpers independently: a real ``get_decoded()``
        failure is raised once and caught by both call sites in turn, since
        both call ``entry.get_decoded()`` on the same entry.
        """
        mock_elf = MagicMock()
        mock_elf.get_machine_arch.return_value = "x64"
        mock_dwarf = MagicMock()
        _fde_with_symbol(mock_dwarf, mock_elf, ValueError("bad decode"))

        meta = AdvancedDwarfMetadata(has_dwarf=True)
        assert _parse_frame_registers(mock_elf, mock_dwarf, meta) is False
        # Neither helper contributed data for the failed FDE.
        assert "exported_fn" not in meta.frame_registers
        assert "exported_fn" not in meta.callee_saved_regs

    def test_only_second_helper_row_processing_fails_is_not_complete(self) -> None:
        """A narrower trigger for the same bug class: the CFA-register
        helper decodes and returns cleanly (no exception at all -- a
        legitimate "no CFA" outcome), but the callee-saved-regs helper's own
        *row processing* (not the initial ``get_decoded()`` call) raises.
        This is the shape the review's own example names explicitly
        (``_extract_callee_saved_regs`` independently catching the same
        error as its sibling) -- must still downgrade ``complete``.
        """
        mock_elf = MagicMock()
        mock_elf.get_machine_arch.return_value = "x64"
        dyn = MagicMock()
        sym = MagicMock()
        sym.name = "exported_fn"
        sym.entry.st_value = 0x1000
        sym.entry.st_info.bind = "STB_GLOBAL"
        dyn.iter_symbols.return_value = [sym]
        mock_elf.get_section_by_name.side_effect = lambda name: (
            dyn if name == ".dynsym" else None
        )

        # A row whose .items() raises on the second call (callee-saved-regs
        # helper) but whose .get() the CFA helper reads fine on the first.
        class _BadRow(dict):
            def items(self):  # noqa: D105
                raise TypeError("corrupt row")

        row = _BadRow({"pc": 0x1000})
        decoded = MagicMock()
        decoded.table = [row]
        fde = MagicMock()
        fde.__class__ = type("FDE", (), {})
        fde.__class__.__name__ = "FDE"
        fde.__getitem__ = MagicMock(return_value=0x1000)
        fde.get_decoded.return_value = decoded
        mock_dwarf = MagicMock()
        mock_dwarf.EH_CFI_entries.return_value = [fde]

        meta = AdvancedDwarfMetadata(has_dwarf=True)
        assert _parse_frame_registers(mock_elf, mock_dwarf, meta) is False
        assert "exported_fn" not in meta.callee_saved_regs


class TestCfiSourceDecodeFailurePropagates:
    """P1 review, fresh evidence (round 3 against this same completeness
    chain): a *present* CFI section whose entries fail to decode (a real
    ``ELFParseError`` from a malformed/truncated ``.eh_frame``/
    ``.debug_frame``) was previously indistinguishable, from
    ``_parse_frame_registers``'s point of view, from "no CFI section at
    all" -- both make ``_get_cfi_source`` return ``None``, and the caller
    unconditionally treated that as a legitimate, complete absence.
    """

    def test_source_decode_failure_is_not_reported_complete(self) -> None:
        from elftools.common.exceptions import ELFParseError

        mock_elf = MagicMock()
        mock_elf.get_machine_arch.return_value = "x64"
        mock_dwarf = MagicMock()
        mock_dwarf.has_EH_CFI.return_value = True
        mock_dwarf.EH_CFI_entries.side_effect = ELFParseError("corrupt eh_frame")
        mock_dwarf.has_CFI.return_value = True
        mock_dwarf.CFI_entries.side_effect = ELFParseError("corrupt debug_frame")

        meta = AdvancedDwarfMetadata(has_dwarf=True)
        assert _parse_frame_registers(mock_elf, mock_dwarf, meta) is False
        assert len(meta.frame_registers) == 0

    def test_total_absence_of_cfi_sections_is_incomplete(self) -> None:
        """P2 review, fresh evidence (Codex, PR #784): a genuine total
        absence of unwind sections (neither .eh_frame nor .debug_frame) is
        now reported incomplete (False), not complete -- both call sites
        of _parse_frame_registers only invoke it once real DWARF DIEs
        exist, so this shape means the unwind sections were stripped
        independently of debug info, not that there was nothing to
        extract. Renamed from test_legitimately_absent_source_is_still_
        reported_complete, whose docstring and assertion described the
        prior (now-corrected) behavior."""
        mock_elf = MagicMock()
        mock_elf.get_machine_arch.return_value = "x64"
        mock_dwarf = MagicMock()
        mock_dwarf.has_EH_CFI.return_value = False
        mock_dwarf.has_CFI.return_value = False

        meta = AdvancedDwarfMetadata(has_dwarf=True)
        assert _parse_frame_registers(mock_elf, mock_dwarf, meta) is False

    def test_eh_frame_decode_failure_with_real_fde_free_debug_frame_fallback(
        self,
    ) -> None:
        """P1 review, fresh evidence (round 4): the reviewer's exact
        reported shape end-to-end through ``_parse_frame_registers`` --
        ``.eh_frame`` fails to decode (recorded via ``source_failed``) and
        the ``.debug_frame`` fallback is present but carries no real FDE
        (CIE-only). ``_get_cfi_source`` previously still returned that
        unusable list as a non-``None`` source, making this function's own
        ``cfi_src is None`` completeness check unreachable and silently
        reporting ``complete=True`` despite the recorded EH-frame failure.
        """
        from elftools.common.exceptions import ELFParseError

        mock_elf = MagicMock()
        mock_elf.get_machine_arch.return_value = "x64"
        mock_dwarf = MagicMock()
        mock_dwarf.has_EH_CFI.return_value = True
        mock_dwarf.EH_CFI_entries.side_effect = ELFParseError("corrupt eh_frame")
        mock_dwarf.has_CFI.return_value = True
        cie_only = MagicMock()
        cie_only.__class__ = type("CIE", (), {})
        mock_dwarf.CFI_entries.return_value = [cie_only]

        meta = AdvancedDwarfMetadata(has_dwarf=True)
        assert _parse_frame_registers(mock_elf, mock_dwarf, meta) is False
        assert len(meta.frame_registers) == 0


def _sym(name: str, addr: int, *, is_func: bool = True) -> MagicMock:
    sym = MagicMock()
    sym.name = name
    sym.entry.st_value = addr
    sym.entry.st_info.bind = "STB_GLOBAL"
    sym.entry.st_info.type = "STT_FUNC" if is_func else "STT_OBJECT"
    return sym


class TestUnmatchedExportedFunctionMarksIncomplete:
    """P2 review, fresh evidence (Codex): partial per-function CFI
    coverage previously returned ``True`` -- when ``.eh_frame`` contains
    valid FDEs but an exported *function* has no matching FDE at all, the
    per-entry loop only ever downgrades ``complete`` for an entry it
    actually iterated; a symbol the loop never saw at all left ``complete``
    untouched. Fixed by tracking the set of exported function addresses
    against every FDE's own ``initial_location`` and downgrading whenever
    any remain unmatched after the loop."""

    def test_exported_function_with_no_fde_marks_incomplete(self) -> None:
        mock_elf = MagicMock()
        mock_elf.get_machine_arch.return_value = "x64"
        dyn = MagicMock()
        covered = _sym("covered_fn", 0x1000)
        uncovered = _sym("uncovered_fn", 0x2000)
        dyn.iter_symbols.return_value = [covered, uncovered]
        mock_elf.get_section_by_name.side_effect = lambda name: (
            dyn if name == ".dynsym" else None
        )

        # Only one FDE, for the covered function -- uncovered_fn's own
        # address never appears as any FDE's initial_location.
        fde = MagicMock()
        fde.__class__ = type("FDE", (), {})
        fde.__class__.__name__ = "FDE"
        fde.__getitem__ = MagicMock(return_value=0x1000)
        decoded = MagicMock()
        decoded.table = []
        fde.get_decoded.return_value = decoded
        mock_dwarf = MagicMock()
        mock_dwarf.EH_CFI_entries.return_value = [fde]

        meta = AdvancedDwarfMetadata(has_dwarf=True)
        assert _parse_frame_registers(mock_elf, mock_dwarf, meta) is False

    def test_exported_data_symbol_is_excluded_from_coverage_set(self) -> None:
        """Positive control, part 1: an exported *data* symbol (STT_OBJECT)
        never has an FDE and is not this analysis' concern -- must not be
        counted against coverage. Checked directly against
        _build_exported_func_addrs rather than end-to-end, which would
        conflate this signal with the separate total-CFI-absence one."""
        mock_elf = MagicMock()
        dyn = MagicMock()
        data_sym = _sym("exported_var", 0x3000, is_func=False)
        dyn.iter_symbols.return_value = [data_sym]
        mock_elf.get_section_by_name.side_effect = lambda name: (
            dyn if name == ".dynsym" else None
        )

        from abicheck.dwarf_advanced import _build_exported_func_addrs

        assert _build_exported_func_addrs(mock_elf) == set()

    def test_every_exported_function_covered_is_not_flagged(self) -> None:
        """Positive control, part 2: every exported function has a
        matching FDE -- must not be flagged."""
        mock_elf = MagicMock()
        mock_elf.get_machine_arch.return_value = "x64"
        dyn = MagicMock()
        sym = _sym("covered_fn", 0x1000)
        dyn.iter_symbols.return_value = [sym]
        mock_elf.get_section_by_name.side_effect = lambda name: (
            dyn if name == ".dynsym" else None
        )

        fde = MagicMock()
        fde.__class__ = type("FDE", (), {})
        fde.__class__.__name__ = "FDE"
        fde.__getitem__ = MagicMock(return_value=0x1000)
        decoded = MagicMock()
        decoded.table = []
        fde.get_decoded.return_value = decoded
        mock_dwarf = MagicMock()
        mock_dwarf.EH_CFI_entries.return_value = [fde]

        meta = AdvancedDwarfMetadata(has_dwarf=True)
        assert _parse_frame_registers(mock_elf, mock_dwarf, meta) is True
