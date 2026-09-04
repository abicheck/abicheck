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
