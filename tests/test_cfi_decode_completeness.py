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


def _fde_for(addr: int, *, cfa_reg: int | None = None) -> MagicMock:
    """Build a mock FDE at *addr*. When *cfa_reg* is given, its decoded
    table carries one real CFA row (register number *cfa_reg*) so
    ``_extract_cfa_reg_from_fde`` resolves an actual register name instead
    of the "no CFA data" ``None`` an empty table always produces."""
    fde = MagicMock()
    fde.__class__ = type("FDE", (), {})
    fde.__class__.__name__ = "FDE"
    fde.__getitem__ = MagicMock(return_value=addr)
    decoded = MagicMock()
    if cfa_reg is None:
        decoded.table = []
    else:
        cfa = MagicMock()
        cfa.reg = cfa_reg
        decoded.table = [{"pc": addr, "cfa": cfa}]
    fde.get_decoded.return_value = decoded
    return fde


class TestMultiSourceCfiCoverage:
    """P2 review, fresh evidence (Codex): a valid binary can link object
    files built with different unwind-table settings, so ``.eh_frame``
    carries FDEs for only some exported functions while ``.debug_frame``
    carries the rest -- ``_get_cfi_source``'s early return on the first
    section with *any* real FDE meant the other section (and whatever
    functions only it named) was never inspected, wrongly counting those
    functions as uncovered. Fixed by ``_get_all_cfi_sources`` collecting
    every section with real FDE data."""

    def test_function_covered_only_by_debug_frame_is_not_flagged(self) -> None:
        mock_elf = MagicMock()
        mock_elf.get_machine_arch.return_value = "x64"
        dyn = MagicMock()
        eh_only = _sym("eh_only_fn", 0x1000)
        dbg_only = _sym("dbg_only_fn", 0x2000)
        dyn.iter_symbols.return_value = [eh_only, dbg_only]
        mock_elf.get_section_by_name.side_effect = lambda name: (
            dyn if name == ".dynsym" else None
        )

        mock_dwarf = MagicMock()
        mock_dwarf.EH_CFI_entries.return_value = [_fde_for(0x1000, cfa_reg=7)]
        mock_dwarf.CFI_entries.return_value = [_fde_for(0x2000, cfa_reg=6)]

        meta = AdvancedDwarfMetadata(has_dwarf=True)
        assert _parse_frame_registers(mock_elf, mock_dwarf, meta) is True
        assert set(meta.frame_registers) == {"eh_only_fn", "dbg_only_fn"}

    def test_function_in_neither_source_still_flagged(self) -> None:
        """Negative control: a function genuinely absent from both sources
        must still be reported incomplete -- the multi-source fix must not
        over-correct into never flagging anything."""
        mock_elf = MagicMock()
        mock_elf.get_machine_arch.return_value = "x64"
        dyn = MagicMock()
        eh_only = _sym("eh_only_fn", 0x1000)
        missing = _sym("missing_fn", 0x3000)
        dyn.iter_symbols.return_value = [eh_only, missing]
        mock_elf.get_section_by_name.side_effect = lambda name: (
            dyn if name == ".dynsym" else None
        )

        mock_dwarf = MagicMock()
        mock_dwarf.EH_CFI_entries.return_value = [_fde_for(0x1000)]
        mock_dwarf.CFI_entries.return_value = [_fde_for(0x2000)]

        meta = AdvancedDwarfMetadata(has_dwarf=True)
        assert _parse_frame_registers(mock_elf, mock_dwarf, meta) is False

    def test_function_in_both_sources_keeps_eh_frame_facts(self) -> None:
        """A function named by both sources must not have its .eh_frame
        (preferred) facts overwritten by .debug_frame's own entry for the
        same address."""
        mock_elf = MagicMock()
        mock_elf.get_machine_arch.return_value = "x64"
        dyn = MagicMock()
        sym = _sym("both_fn", 0x1000)
        dyn.iter_symbols.return_value = [sym]
        mock_elf.get_section_by_name.side_effect = lambda name: (
            dyn if name == ".dynsym" else None
        )

        eh_fde = _fde_for(0x1000, cfa_reg=7)
        dbg_fde = _fde_for(0x1000)
        mock_dwarf = MagicMock()
        mock_dwarf.EH_CFI_entries.return_value = [eh_fde]
        mock_dwarf.CFI_entries.return_value = [dbg_fde]

        meta = AdvancedDwarfMetadata(has_dwarf=True)
        assert _parse_frame_registers(mock_elf, mock_dwarf, meta) is True
        assert set(meta.frame_registers) == {"both_fn"}
        # The .debug_frame duplicate's own FDE must never have been decoded
        # for facts -- only consulted for coverage (its initial_location).
        dbg_fde.get_decoded.assert_not_called()
        eh_fde.get_decoded.assert_called()


class TestEveryExportedAliasReceivesCfiFacts:
    """P1 review, fresh evidence (Codex): multiple exported names can
    legitimately share one address (a strong/weak symbol pair, or several
    public entry points the linker folded onto identical code).
    ``_parse_frame_registers`` previously resolved only ONE symbol name
    per address (``_build_addr_to_sym``'s own first-seen-wins mapping),
    attached this FDE's facts to that one name, then marked the *address*
    covered -- so every OTHER exported alias at that same address silently
    never received its own ``frame_registers``/``callee_saved_regs`` entry
    at all, indistinguishable from "this function has no CFI facts",
    while the pass still reported itself complete."""

    def test_all_aliases_at_one_address_receive_facts(self) -> None:
        mock_elf = MagicMock()
        mock_elf.get_machine_arch.return_value = "x64"
        dyn = MagicMock()
        alias_a = _sym("alias_a", 0x1000)
        alias_b = _sym("alias_b", 0x1000)
        dyn.iter_symbols.return_value = [alias_a, alias_b]
        mock_elf.get_section_by_name.side_effect = lambda name: (
            dyn if name == ".dynsym" else None
        )

        fde = _fde_for(0x1000, cfa_reg=7)
        mock_dwarf = MagicMock()
        mock_dwarf.EH_CFI_entries.return_value = [fde]

        meta = AdvancedDwarfMetadata(has_dwarf=True)
        assert _parse_frame_registers(mock_elf, mock_dwarf, meta) is True
        # Both aliases -- not just whichever one a first-seen-wins address
        # map happened to keep -- must carry the decoded CFA-register fact.
        assert set(meta.frame_registers) == {"alias_a", "alias_b"}
        assert meta.frame_registers["alias_a"] == meta.frame_registers["alias_b"]

    def test_single_symbol_at_an_address_is_unaffected(self) -> None:
        """Positive control: the ordinary one-name-per-address case must
        behave exactly as before -- proving the fix didn't disturb it."""
        mock_elf = MagicMock()
        mock_elf.get_machine_arch.return_value = "x64"
        dyn = MagicMock()
        sym = _sym("solo_fn", 0x1000)
        dyn.iter_symbols.return_value = [sym]
        mock_elf.get_section_by_name.side_effect = lambda name: (
            dyn if name == ".dynsym" else None
        )

        fde = _fde_for(0x1000, cfa_reg=7)
        mock_dwarf = MagicMock()
        mock_dwarf.EH_CFI_entries.return_value = [fde]

        meta = AdvancedDwarfMetadata(has_dwarf=True)
        assert _parse_frame_registers(mock_elf, mock_dwarf, meta) is True
        assert set(meta.frame_registers) == {"solo_fn"}


class TestFunctionDescriptorArchExcludedFromCoverage:
    """P1 review, fresh evidence (Codex): on big-endian PPC64 ELFv1, an
    exported STT_FUNC symbol's ``st_value`` points to its ``.opd`` function
    descriptor, not its code entry, so it can never equal any FDE's own
    ``initial_location`` -- every exported function on such a binary would
    wrongly read "uncovered" under the naive address-equality check. Fixed
    by excluding the documented function-descriptor architectures
    (``_FUNCTION_DESCRIPTOR_ARCHES``) from the coverage set.

    A second P1 review round (fresh evidence, Codex) found the original
    fix over-corrected: it excluded coverage for every architecture this
    module lacks a register-name table for (an ALLOWLIST keyed off
    ``_reg_name``'s own x64/x86/aarch64 set), which has nothing to do with
    whether ``st_value`` names a real code entry -- silently disabling the
    check for RISC-V, ARM32, MIPS, and every other direct-entry
    architecture too. Flipped to a denylist of only the documented
    function-descriptor ABIs; see
    TestOtherArchitecturesGetCoverageByDefault below for the fix proving
    a previously-excluded direct-entry architecture (RISC-V) is now
    correctly tracked."""

    def test_ppc64_with_no_matching_fde_is_not_flagged(self) -> None:
        # "64-bit PowerPC" is pyelftools' own get_machine_arch() spelling
        # for EM_PPC64 -- _normalize_arch has no remap entry for it, so it
        # passes through verbatim.
        mock_elf = MagicMock()
        mock_elf.get_machine_arch.return_value = "64-bit PowerPC"
        dyn = MagicMock()
        # st_value (0x1000) deliberately never matches the FDE's own
        # initial_location (0x2000) -- the exact PPC64 .opd-descriptor
        # mismatch shape, without needing real .opd-resolution logic.
        sym = _sym("ppc64_fn", 0x1000)
        dyn.iter_symbols.return_value = [sym]
        mock_elf.get_section_by_name.side_effect = lambda name: (
            dyn if name == ".dynsym" else None
        )

        mock_dwarf = MagicMock()
        mock_dwarf.EH_CFI_entries.return_value = [_fde_for(0x2000)]

        meta = AdvancedDwarfMetadata(has_dwarf=True)
        assert _parse_frame_registers(mock_elf, mock_dwarf, meta) is True

    def test_known_arch_with_same_mismatch_is_still_flagged(self) -> None:
        """Positive control: the identical address mismatch on a
        known/verified architecture (x64) must still be flagged -- the
        arch restriction must not blanket-disable this check."""
        mock_elf = MagicMock()
        mock_elf.get_machine_arch.return_value = "x64"
        dyn = MagicMock()
        sym = _sym("x64_fn", 0x1000)
        dyn.iter_symbols.return_value = [sym]
        mock_elf.get_section_by_name.side_effect = lambda name: (
            dyn if name == ".dynsym" else None
        )

        mock_dwarf = MagicMock()
        mock_dwarf.EH_CFI_entries.return_value = [_fde_for(0x2000)]

        meta = AdvancedDwarfMetadata(has_dwarf=True)
        assert _parse_frame_registers(mock_elf, mock_dwarf, meta) is False


class TestOtherArchitecturesGetCoverageByDefault:
    """P1 review, fresh evidence (Codex): a direct-entry architecture this
    module has no register-name table for (e.g. RISC-V) must still get
    the coverage check -- only the documented function-descriptor ABIs in
    _FUNCTION_DESCRIPTOR_ARCHES are excluded now, not every architecture
    outside a small register-name-table allowlist."""

    def test_riscv_with_no_matching_fde_is_flagged(self) -> None:
        mock_elf = MagicMock()
        # "RISC-V" is pyelftools' own get_machine_arch() spelling for
        # EM_RISCV.
        mock_elf.get_machine_arch.return_value = "RISC-V"
        dyn = MagicMock()
        sym = _sym("riscv_fn", 0x1000)
        dyn.iter_symbols.return_value = [sym]
        mock_elf.get_section_by_name.side_effect = lambda name: (
            dyn if name == ".dynsym" else None
        )

        mock_dwarf = MagicMock()
        mock_dwarf.EH_CFI_entries.return_value = [_fde_for(0x2000)]

        meta = AdvancedDwarfMetadata(has_dwarf=True)
        assert _parse_frame_registers(mock_elf, mock_dwarf, meta) is False

    def test_riscv_fully_covered_is_not_flagged(self) -> None:
        """Positive control: a fully-covered RISC-V binary must not be
        wrongly flagged -- proving the fix only adds detection, not false
        positives on the ordinary covered case."""
        mock_elf = MagicMock()
        mock_elf.get_machine_arch.return_value = "RISC-V"
        dyn = MagicMock()
        sym = _sym("riscv_fn", 0x1000)
        dyn.iter_symbols.return_value = [sym]
        mock_elf.get_section_by_name.side_effect = lambda name: (
            dyn if name == ".dynsym" else None
        )

        mock_dwarf = MagicMock()
        mock_dwarf.EH_CFI_entries.return_value = [_fde_for(0x1000)]

        meta = AdvancedDwarfMetadata(has_dwarf=True)
        assert _parse_frame_registers(mock_elf, mock_dwarf, meta) is True
