# pylint: disable=too-many-branches,too-many-statements,too-many-locals,too-many-arguments,too-many-return-statements
"""Sprint 4 tests: advanced DWARF detectors (calling convention, packing, toolchain drift)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.compare.dwarf_advanced_diff import diff_advanced_dwarf
from abicheck.dwarf_advanced import (
    AdvancedDwarfMetadata,
    ToolchainInfo,
    _parse_producer,
    parse_advanced_dwarf,
)
from abicheck.model import AbiSnapshot
from abicheck.serialization import (
    snapshot_from_dict,
    snapshot_to_dict,
    snapshot_to_json,
)
from tests.test_dwarf_metadata_coverage import _CU, _Attr, _Die


class TestParseAdvancedDwarfEvidenceState:
    """P2 review: parse_advanced_dwarf must record its own extraction
    outcome (cu_total/cu_failed -> evidence_state), mirroring
    dwarf_unified.parse_dwarf_from_session's accounting -- previously this
    standalone, still-public entry point never touched evidence_state at
    all, leaving it at the "not_available" dataclass default even on a
    fully successful parse."""

    def _mock_session(self, cus: list) -> MagicMock:
        mock_elf = MagicMock()
        mock_dwarf = MagicMock()
        mock_dwarf.iter_CUs.return_value = cus
        mock_elf.get_dwarf_info.return_value = mock_dwarf
        return mock_elf

    def test_one_of_two_cus_failing_reports_partial(self) -> None:
        bad_cu = MagicMock()
        bad_cu.get_top_DIE.side_effect = ValueError("corrupt CU")
        good_cu = MagicMock()
        good_cu.get_top_DIE.return_value = MagicMock(attributes={})

        mock_elf = self._mock_session([bad_cu, good_cu])
        with (
            patch("abicheck.dwarf_advanced.ELFFile", return_value=mock_elf),
            patch("abicheck.dwarf_advanced.has_real_dwarf_info", return_value=True),
            patch("abicheck.dwarf_advanced._parse_frame_registers"),
        ):
            meta = parse_advanced_dwarf(Path(__file__))

        assert meta.has_dwarf is True
        assert meta.cu_total == 2
        assert meta.cu_failed == 1
        assert meta.evidence_state == "partial"

    def test_every_cu_failing_reports_failed(self) -> None:
        bad_cu = MagicMock()
        bad_cu.get_top_DIE.side_effect = ValueError("corrupt CU")

        mock_elf = self._mock_session([bad_cu])
        with (
            patch("abicheck.dwarf_advanced.ELFFile", return_value=mock_elf),
            patch("abicheck.dwarf_advanced.has_real_dwarf_info", return_value=True),
            patch("abicheck.dwarf_advanced._parse_frame_registers"),
        ):
            meta = parse_advanced_dwarf(Path(__file__))

        assert meta.cu_total == 1
        assert meta.cu_failed == 1
        assert meta.evidence_state == "failed"

    def test_clean_parse_reports_parsed(self) -> None:
        good_cu = MagicMock()
        good_cu.get_top_DIE.return_value = MagicMock(attributes={})

        mock_elf = self._mock_session([good_cu])
        with (
            patch("abicheck.dwarf_advanced.ELFFile", return_value=mock_elf),
            patch("abicheck.dwarf_advanced.has_real_dwarf_info", return_value=True),
            patch("abicheck.dwarf_advanced._parse_frame_registers"),
        ):
            meta = parse_advanced_dwarf(Path(__file__))

        assert meta.cu_total == 1
        assert meta.cu_failed == 0
        assert meta.evidence_state == "parsed"

    def test_no_unwind_sections_at_all_downgrades_a_clean_parse_to_partial(
        self,
    ) -> None:
        """P2 review, fresh evidence (Codex): a binary with real DWARF DIEs
        but neither .eh_frame nor .debug_frame present at all (independently
        stripped unwind sections) previously reported evidence_state=
        "parsed" -- frame_registers/callee_saved_regs stay empty for every
        function with no completeness signal. Exercised through the real
        public entry point (parse_advanced_dwarf), letting the actual
        _parse_frame_registers/_get_cfi_source pipeline run rather than
        patching it out."""
        good_cu = MagicMock()
        good_cu.get_top_DIE.return_value = MagicMock(attributes={})

        mock_elf = self._mock_session([good_cu])
        mock_dwarf = mock_elf.get_dwarf_info.return_value
        mock_dwarf.has_EH_CFI.return_value = False
        mock_dwarf.has_CFI.return_value = False

        with (
            patch("abicheck.dwarf_advanced.ELFFile", return_value=mock_elf),
            patch("abicheck.dwarf_advanced.has_real_dwarf_info", return_value=True),
            patch("abicheck.dwarf_advanced._normalize_arch", return_value="x86_64"),
            patch("abicheck.dwarf_advanced._build_addr_to_sym", return_value={}),
        ):
            meta = parse_advanced_dwarf(Path(__file__))

        assert meta.cu_failed == 0
        assert meta.evidence_state == "partial"
        assert meta.frame_registers == {}

    def test_incomplete_cfi_downgrades_a_clean_parse_to_partial(self) -> None:
        """P1 review, fresh evidence: _parse_frame_registers previously
        exposed no completion signal at all, so a malformed/unsupported FDE
        it caught and skipped internally left evidence_state at whatever
        the (otherwise clean) CU accounting decided -- "parsed", despite
        frame-register/callee-saved-register facts for that FDE never
        being extracted."""
        good_cu = MagicMock()
        good_cu.get_top_DIE.return_value = MagicMock(attributes={})

        mock_elf = self._mock_session([good_cu])
        with (
            patch("abicheck.dwarf_advanced.ELFFile", return_value=mock_elf),
            patch("abicheck.dwarf_advanced.has_real_dwarf_info", return_value=True),
            patch("abicheck.dwarf_advanced._parse_frame_registers", return_value=False),
        ):
            meta = parse_advanced_dwarf(Path(__file__))

        assert meta.cu_failed == 0
        assert meta.evidence_state == "partial"

    def test_incomplete_cfi_never_upgrades_an_already_failed_parse(self) -> None:
        """Downgrading must only ever apply to a clean "parsed" state --
        it must not paper over (or otherwise disturb) a worse state the CU
        accounting already decided."""
        bad_cu = MagicMock()
        bad_cu.get_top_DIE.side_effect = ValueError("corrupt CU")

        mock_elf = self._mock_session([bad_cu])
        with (
            patch("abicheck.dwarf_advanced.ELFFile", return_value=mock_elf),
            patch("abicheck.dwarf_advanced.has_real_dwarf_info", return_value=True),
            patch("abicheck.dwarf_advanced._parse_frame_registers", return_value=False),
        ):
            meta = parse_advanced_dwarf(Path(__file__))

        assert meta.evidence_state == "failed"


class TestValueAbiTraitIncompletePropagation:
    """P1 review, fresh evidence (Codex): a malformed DW_AT_type on an
    exported function's return/parameter type -- caught deep inside the
    value-ABI-trait walk (resolve_type_die/_unwrap_qualifiers/
    _is_nontrivial_aggregate/_type_unaligned_at, each returning a
    placeholder rather than raising) -- previously left cu_failed
    untouched and evidence_state at "parsed", silently omitting that
    function's value_abi_traits/return_value_sizes/
    return_memory_classified entries with no completeness signal.
    Exercised through the public parse_advanced_dwarf entry point, using
    real DIE fixtures (not MagicMock) so a genuinely unresolvable
    DW_AT_type reference reproduces the same way pyelftools' own
    get_DIE_from_refaddr does (it raises, it does not return None)."""

    def test_malformed_return_type_marks_partial(self) -> None:
        subprogram = _Die(
            "DW_TAG_subprogram",
            {
                "DW_AT_external": _Attr(1),
                "DW_AT_linkage_name": "_Z3foov",
                "DW_AT_type": _Attr(999, "DW_FORM_ref_addr"),
            },
        )
        root = _Die("DW_TAG_compile_unit", children=[subprogram])
        cu = _CU(top_die=root, offset=0)
        cu._die_map = {}

        mock_elf = MagicMock()
        mock_dwarf = MagicMock()
        mock_dwarf.iter_CUs.return_value = [cu]
        mock_elf.get_dwarf_info.return_value = mock_dwarf

        with (
            patch("abicheck.dwarf_advanced.ELFFile", return_value=mock_elf),
            patch("abicheck.dwarf_advanced.has_real_dwarf_info", return_value=True),
            patch("abicheck.dwarf_advanced._parse_frame_registers", return_value=True),
            patch(
                "abicheck.dwarf_utils.resolve_die_ref",
                side_effect=RuntimeError("bad ref"),
            ),
        ):
            meta = parse_advanced_dwarf(Path(__file__))

        assert meta.cu_failed == 0
        assert meta.evidence_state == "partial"
        # The return type never resolved -- no ret: trait, and with no
        # params either, this function gets no value_abi_traits entry at
        # all, same as a "nothing ABI-relevant here" function would.
        assert "_Z3foov" not in meta.value_abi_traits

    def test_malformed_parameter_type_marks_partial(self) -> None:
        """Sibling shape: the return type resolves fine (a plain scalar,
        contributing no trait), but one formal parameter's type does not."""
        int_type = _Die(
            "DW_TAG_base_type", {"DW_AT_name": "int", "DW_AT_byte_size": 4}, offset=10
        )
        good_param = _Die(
            "DW_TAG_formal_parameter", {"DW_AT_type": _Attr(10, "DW_FORM_ref_addr")}
        )
        bad_param = _Die(
            "DW_TAG_formal_parameter", {"DW_AT_type": _Attr(999, "DW_FORM_ref_addr")}
        )
        subprogram = _Die(
            "DW_TAG_subprogram",
            {
                "DW_AT_external": _Attr(1),
                "DW_AT_linkage_name": "_Z3fooiP1S",
                "DW_AT_type": _Attr(10, "DW_FORM_ref_addr"),
            },
            children=[good_param, bad_param],
        )
        root = _Die("DW_TAG_compile_unit", children=[subprogram])
        cu = _CU(top_die=root, offset=0)
        cu._die_map = {10: int_type}

        real_resolve = __import__(
            "abicheck.dwarf_utils", fromlist=["resolve_die_ref"]
        ).resolve_die_ref

        def _selective_resolve(die, attr_name, CU):  # noqa: ANN001, N803
            if die.attributes[attr_name].value == 999:
                raise RuntimeError("bad ref")
            return real_resolve(die, attr_name, CU)

        mock_elf = MagicMock()
        mock_dwarf = MagicMock()
        mock_dwarf.iter_CUs.return_value = [cu]
        mock_elf.get_dwarf_info.return_value = mock_dwarf

        with (
            patch("abicheck.dwarf_advanced.ELFFile", return_value=mock_elf),
            patch("abicheck.dwarf_advanced.has_real_dwarf_info", return_value=True),
            patch("abicheck.dwarf_advanced._parse_frame_registers", return_value=True),
            patch(
                "abicheck.dwarf_utils.resolve_die_ref", side_effect=_selective_resolve
            ),
        ):
            meta = parse_advanced_dwarf(Path(__file__))

        assert meta.cu_failed == 0
        assert meta.evidence_state == "partial"
        # int return type isn't an aggregate -> no ret: trait either, and
        # the resolvable param (int, also non-aggregate) contributes
        # nothing -- the malformed param is the only thing that could have
        # produced a trait, so this function gets no traits entry at all,
        # same as the return-type case above.
        assert "_Z3fooiP1S" not in meta.value_abi_traits

    def test_clean_function_traits_are_not_flagged(self) -> None:
        """Positive control: a fully-resolvable by-value struct return type
        (a real value-ABI-relevant shape) must not be flagged, and its
        trait must still be recorded correctly."""
        struct_type = _Die(
            "DW_TAG_structure_type",
            {"DW_AT_name": "S", "DW_AT_byte_size": 8},
            offset=10,
        )
        subprogram = _Die(
            "DW_TAG_subprogram",
            {
                "DW_AT_external": _Attr(1),
                "DW_AT_linkage_name": "_Z3barv",
                "DW_AT_type": _Attr(10, "DW_FORM_ref_addr"),
            },
        )
        root = _Die("DW_TAG_compile_unit", children=[subprogram])
        cu = _CU(top_die=root, offset=0)
        cu._die_map = {10: struct_type}

        mock_elf = MagicMock()
        mock_dwarf = MagicMock()
        mock_dwarf.iter_CUs.return_value = [cu]
        mock_elf.get_dwarf_info.return_value = mock_dwarf

        with (
            patch("abicheck.dwarf_advanced.ELFFile", return_value=mock_elf),
            patch("abicheck.dwarf_advanced.has_real_dwarf_info", return_value=True),
            patch("abicheck.dwarf_advanced._parse_frame_registers", return_value=True),
        ):
            meta = parse_advanced_dwarf(Path(__file__))

        assert meta.cu_failed == 0
        assert meta.evidence_state == "parsed"
        assert meta.value_abi_traits["_Z3barv"] == "ret:trivial"


class TestPackedTypedefIncompletePropagation:
    """P1 review, fresh evidence (Codex): _walk_cu threaded `incomplete`
    into the calling-convention path only -- the separate anonymous-
    struct-typedef packed-check walk (_check_packed_typedef) still caught
    a malformed DW_AT_type and returned silently, so both the unified and
    standalone parsers could report advanced evidence "parsed" while
    omitting that typedef's packing facts."""

    def test_malformed_typedef_target_marks_partial(self) -> None:
        typedef = _Die(
            "DW_TAG_typedef",
            {"DW_AT_name": "MyAlias", "DW_AT_type": _Attr(999, "DW_FORM_ref_addr")},
        )
        root = _Die("DW_TAG_compile_unit", children=[typedef])
        cu = _CU(top_die=root, offset=0)
        cu._die_map = {}

        mock_elf = MagicMock()
        mock_dwarf = MagicMock()
        mock_dwarf.iter_CUs.return_value = [cu]
        mock_elf.get_dwarf_info.return_value = mock_dwarf

        with (
            patch("abicheck.dwarf_advanced.ELFFile", return_value=mock_elf),
            patch("abicheck.dwarf_advanced.has_real_dwarf_info", return_value=True),
            patch("abicheck.dwarf_advanced._parse_frame_registers", return_value=True),
            patch(
                "abicheck.dwarf_advanced._resolve_die_ref",
                side_effect=RuntimeError("bad ref"),
            ),
        ):
            meta = parse_advanced_dwarf(Path(__file__))

        assert meta.cu_failed == 0
        assert meta.evidence_state == "partial"
        assert "MyAlias" not in meta.all_struct_names

    def test_resolvable_packed_typedef_is_not_flagged(self) -> None:
        """Positive control: a fully-resolvable anonymous packed struct
        typedef must not be flagged, and its packing must still be
        recorded correctly."""
        member = _Die(
            "DW_TAG_member",
            {"DW_AT_name": "c", "DW_AT_type": _Attr(20, "DW_FORM_ref_addr")},
        )
        member2 = _Die(
            "DW_TAG_member",
            {
                "DW_AT_name": "i",
                "DW_AT_type": _Attr(21, "DW_FORM_ref_addr"),
                "DW_AT_data_member_location": _Attr(1),
            },
        )
        anon_struct = _Die(
            "DW_TAG_structure_type",
            {"DW_AT_byte_size": 5},
            offset=10,
            children=[member, member2],
        )
        typedef = _Die(
            "DW_TAG_typedef",
            {"DW_AT_name": "Packed", "DW_AT_type": _Attr(10, "DW_FORM_ref_addr")},
        )
        root = _Die("DW_TAG_compile_unit", children=[typedef])
        cu = _CU(top_die=root, offset=0)
        char_type = _Die(
            "DW_TAG_base_type", {"DW_AT_name": "char", "DW_AT_byte_size": 1}, offset=20
        )
        int_type = _Die(
            "DW_TAG_base_type", {"DW_AT_name": "int", "DW_AT_byte_size": 4}, offset=21
        )
        cu._die_map = {10: anon_struct, 20: char_type, 21: int_type}

        mock_elf = MagicMock()
        mock_dwarf = MagicMock()
        mock_dwarf.iter_CUs.return_value = [cu]
        mock_elf.get_dwarf_info.return_value = mock_dwarf

        with (
            patch("abicheck.dwarf_advanced.ELFFile", return_value=mock_elf),
            patch("abicheck.dwarf_advanced.has_real_dwarf_info", return_value=True),
            patch("abicheck.dwarf_advanced._parse_frame_registers", return_value=True),
        ):
            meta = parse_advanced_dwarf(Path(__file__))

        assert meta.cu_failed == 0
        assert meta.evidence_state == "parsed"
        assert "Packed" in meta.packed_structs


# ── helpers ──────────────────────────────────────────────────────────────────


def _snap(adv: AdvancedDwarfMetadata | None) -> AbiSnapshot:
    s = AbiSnapshot(library="libx.so", version="v")
    s.dwarf_advanced = adv  # type: ignore[attr-defined]
    return s


def _adv(
    *,
    has_dwarf: bool = True,
    target_arch: str = "",
    calling: dict[str, str] | None = None,
    value_traits: dict[str, str] | None = None,
    packed: set[str] | None = None,
    flags: set[str] | None = None,
    all_structs: set[str] | None = None,
    frame_regs: dict[str, str] | None = None,
    callee_saved: dict[str, frozenset[str]] | None = None,
) -> AdvancedDwarfMetadata:
    packed_set = packed or set()
    # all_struct_names must include packed structs so diff guards work correctly
    struct_names = (all_structs or set()) | packed_set
    return AdvancedDwarfMetadata(
        has_dwarf=has_dwarf,
        target_arch=target_arch,
        toolchain=ToolchainInfo(
            producer_string="gcc",
            compiler="GCC",
            version="13.2",
            abi_flags=flags or set(),
        ),
        calling_conventions=calling or {},
        value_abi_traits=value_traits or {},
        packed_structs=packed_set,
        all_struct_names=struct_names,
        frame_registers=frame_regs or {},
        callee_saved_regs=callee_saved or {},
    )


# ── graceful degradation ──────────────────────────────────────────────────────


def test_diff_advanced_dwarf_no_dwarf() -> None:
    old = _adv(has_dwarf=False)
    new = _adv(has_dwarf=True)
    assert diff_advanced_dwarf(old, new) == []


def test_diff_both_no_dwarf() -> None:
    old = _adv(has_dwarf=False)
    new = _adv(has_dwarf=False)
    assert diff_advanced_dwarf(old, new) == []


# ── calling convention ────────────────────────────────────────────────────────


def test_calling_convention_changed() -> None:
    old = _snap(_adv(calling={"foo": "program"}))
    new = _snap(_adv(calling={"foo": "normal"}))
    r = compare(old, new)
    kinds = {c.kind for c in r.changes}
    assert ChangeKind.CALLING_CONVENTION_CHANGED in kinds
    assert r.verdict == Verdict.BREAKING


def test_calling_convention_added_non_default() -> None:
    # Both binaries have "foo" (present in both dicts); old is normal, new is vectorcall.
    # With full-dict storage, "normal" must be explicit so diff knows foo existed in old.
    old = _snap(_adv(calling={"foo": "normal"}))
    new = _snap(_adv(calling={"foo": "LLVM_vectorcall"}))
    r = compare(old, new)
    kinds = {c.kind for c in r.changes}
    assert ChangeKind.CALLING_CONVENTION_CHANGED in kinds


def test_calling_convention_between_non_defaults() -> None:
    """Changed from one non-normal CC to another."""
    results = diff_advanced_dwarf(
        _adv(calling={"bar": "program"}),
        _adv(calling={"bar": "LLVM_vectorcall"}),
    )
    assert len(results) == 1
    assert results[0][0] == "calling_convention_changed"
    assert results[0][1] == "bar"
    assert results[0][3] == "program"
    assert results[0][4] == "LLVM_vectorcall"


def test_calling_convention_removed() -> None:
    """Non-default CC dropped back to normal (function still exists in both binaries)."""
    # Both dicts contain "foo": old has non-standard CC, new has "normal" explicitly.
    # This represents a function that changed CC, not a removed function.
    results = diff_advanced_dwarf(
        _adv(calling={"foo": "BORLAND_stdcall"}),
        _adv(calling={"foo": "normal"}),
    )
    assert len(results) == 1
    assert results[0][0] == "calling_convention_changed"
    assert results[0][3] == "BORLAND_stdcall"
    assert results[0][4] == "normal"


def test_calling_convention_unchanged_no_change() -> None:
    results = diff_advanced_dwarf(
        _adv(calling={"f": "program"}),
        _adv(calling={"f": "program"}),
    )
    assert results == []


def test_value_abi_trait_changed_breaking() -> None:
    # A parameter-position triviality flip (ret unchanged) stays a generic
    # value-ABI trait change; a *return*-position flip is the more specific
    # struct_return_convention_changed (see test below).
    old = _snap(_adv(value_traits={"foo": "ret:v(trivial)|p0:trivial"}))
    new = _snap(_adv(value_traits={"foo": "ret:v(trivial)|p0:nontrivial"}))
    r = compare(old, new)
    kinds = {c.kind for c in r.changes}
    assert ChangeKind.VALUE_ABI_TRAIT_CHANGED in kinds
    assert r.verdict == Verdict.BREAKING


def test_return_trait_flip_is_struct_return_convention_changed() -> None:
    # A return-position triviality flip means the aggregate moved between
    # in-register return and hidden sret pointer — struct_return_convention_changed.
    old = _snap(_adv(value_traits={"foo": "ret:v(trivial)"}))
    new = _snap(_adv(value_traits={"foo": "ret:v(nontrivial)"}))
    r = compare(old, new)
    kinds = {c.kind for c in r.changes}
    assert ChangeKind.STRUCT_RETURN_CONVENTION_CHANGED in kinds
    assert ChangeKind.VALUE_ABI_TRAIT_CHANGED not in kinds
    assert r.verdict == Verdict.BREAKING


def test_return_trait_flip_on_sysv_amd64_arch_is_convention_change() -> None:
    # Explicit x86_64 arch → SysV AMD64 model, sret-flip classification applies.
    old = _snap(_adv(target_arch="x86_64", value_traits={"foo": "ret:v(trivial)"}))
    new = _snap(_adv(target_arch="x86_64", value_traits={"foo": "ret:v(nontrivial)"}))
    r = compare(old, new)
    kinds = {c.kind for c in r.changes}
    assert ChangeKind.STRUCT_RETURN_CONVENTION_CHANGED in kinds
    assert ChangeKind.VALUE_ABI_TRAIT_CHANGED not in kinds


def test_return_trait_flip_on_non_sysv_arch_is_generic_trait_change() -> None:
    # On AArch64 an HFA can be returned in vector registers despite being >16
    # bytes; on i386 every aggregate is memory-returned. The SysV-AMD64 16-byte
    # register model does not apply, so a return-triviality flip is reported as a
    # generic value-ABI trait change rather than a register<->sret convention flip.
    for arch in ("aarch64", "i386"):
        old = _snap(_adv(target_arch=arch, value_traits={"foo": "ret:v(trivial)"}))
        new = _snap(_adv(target_arch=arch, value_traits={"foo": "ret:v(nontrivial)"}))
        r = compare(old, new)
        kinds = {c.kind for c in r.changes}
        assert ChangeKind.STRUCT_RETURN_CONVENTION_CHANGED not in kinds, arch
        assert ChangeKind.VALUE_ABI_TRAIT_CHANGED in kinds, arch
        # Still a value-ABI change → still breaking.
        assert r.verdict == Verdict.BREAKING


def test_return_trait_flip_mixed_arch_falls_back_to_generic() -> None:
    # If one side's arch is a known non-SysV target, do not claim a convention
    # flip — only when BOTH sides use the SysV-AMD64 return model.
    old = _snap(_adv(target_arch="x86_64", value_traits={"foo": "ret:v(trivial)"}))
    new = _snap(_adv(target_arch="aarch64", value_traits={"foo": "ret:v(nontrivial)"}))
    r = compare(old, new)
    kinds = {c.kind for c in r.changes}
    assert ChangeKind.STRUCT_RETURN_CONVENTION_CHANGED not in kinds
    assert ChangeKind.VALUE_ABI_TRAIT_CHANGED in kinds


def test_target_arch_round_trips_through_serialization() -> None:
    snap = _snap(_adv(target_arch="aarch64", value_traits={"foo": "ret:v(trivial)"}))
    restored = snapshot_from_dict(snapshot_to_dict(snap))
    assert restored.dwarf_advanced.target_arch == "aarch64"  # type: ignore[attr-defined]


def test_callee_saved_fallback_detects_calling_convention_drift() -> None:
    """ELF CFI fallback: saved rdi/rsi indicates ms_abi shift."""
    old = _snap(_adv(callee_saved={"foo": frozenset({"rbx", "rbp", "r12"})}))
    new = _snap(
        _adv(callee_saved={"foo": frozenset({"rbx", "rbp", "r12", "rdi", "rsi"})})
    )
    r = compare(old, new)
    kinds = {c.kind for c in r.changes}
    assert ChangeKind.CALLING_CONVENTION_CHANGED in kinds
    assert r.verdict == Verdict.BREAKING


def test_callee_saved_fallback_ignores_non_marker_register_churn() -> None:
    """rbx/r12 churn alone is not enough to claim calling-convention drift."""
    old = _snap(_adv(callee_saved={"foo": frozenset({"rbx", "rbp", "r12"})}))
    new = _snap(_adv(callee_saved={"foo": frozenset({"rbx", "rbp", "r12", "r13"})}))
    r = compare(old, new)
    kinds = {c.kind for c in r.changes}
    assert ChangeKind.CALLING_CONVENTION_CHANGED not in kinds


def test_extract_callee_saved_regs_mocked() -> None:
    """Test _extract_callee_saved_regs with a mocked FDE."""
    from abicheck.dwarf_advanced import _extract_callee_saved_regs

    class MockRow:
        def __init__(self, pc, regs):
            self.pc = pc
            self.regs = regs

        def items(self):
            return self.regs.items()

    class MockRule:
        def __init__(self, typ):
            self.type = typ

    class MockTable:
        table = [
            MockRow(pc=0x1000, regs={16: MockRule("offset")}),
            MockRow(pc=0x1004, regs={3: MockRule("offset"), 4: MockRule("undefined")}),
        ]

    class MockDecoded:
        table = MockTable.table

    class MockEntry:
        def get_decoded(self):
            return MockDecoded()

    entry = MockEntry()
    result = _extract_callee_saved_regs(entry, "x86_64")
    # x86_64: reg 16 = rip, reg 3 = rbx, reg 4 = rsi (but undefined → not saved)
    assert result == frozenset({"rip", "rbx"})


def test_value_abi_trait_unchanged_no_change() -> None:
    results = diff_advanced_dwarf(
        _adv(value_traits={"foo": "ret:v(trivial)|p0:v(trivial)"}),
        _adv(value_traits={"foo": "ret:v(trivial)|p0:v(trivial)"}),
    )
    assert not any(r[0] == "value_abi_trait_changed" for r in results)


# ── struct packing ────────────────────────────────────────────────────────────


def test_struct_packing_added() -> None:
    # "Ctx" must exist in old all_struct_names so diff knows it's a pre-existing
    # struct that became packed (not a brand-new packed struct, which has no ABI contract).
    old = _snap(_adv(packed=set(), all_structs={"Ctx"}))
    new = _snap(_adv(packed={"Ctx"}))
    r = compare(old, new)
    kinds = {c.kind for c in r.changes}
    assert ChangeKind.STRUCT_PACKING_CHANGED in kinds
    assert r.verdict == Verdict.BREAKING


def test_struct_packing_added_new_struct_no_report() -> None:
    """Brand-new packed struct (not in old binary) should NOT report packing change."""
    old = _snap(_adv(packed=set()))  # "Ctx" never existed in old
    new = _snap(_adv(packed={"Ctx"}))
    r = compare(old, new)
    kinds = {c.kind for c in r.changes}
    assert ChangeKind.STRUCT_PACKING_CHANGED not in kinds


def test_struct_packing_removed() -> None:
    """packed→unpacked is a breaking layout change when the struct still exists.

    all_structs must be set on the new side to prove the struct still exists
    (not removed). Without it the diff guard would skip the report to avoid
    false positives from struct deletion.
    """
    old = _snap(_adv(packed={"Hdr"}))
    new = _snap(_adv(packed=set(), all_structs={"Hdr"}))
    r = compare(old, new)
    kinds = {c.kind for c in r.changes}
    assert ChangeKind.STRUCT_PACKING_CHANGED in kinds
    assert r.verdict == Verdict.BREAKING


def test_struct_packing_unchanged_no_change() -> None:
    results = diff_advanced_dwarf(
        _adv(packed={"A", "B"}),
        _adv(packed={"A", "B"}),
    )
    assert not any(r[0] == "struct_packing_changed" for r in results)


# ── toolchain flag drift ──────────────────────────────────────────────────────


def test_toolchain_flag_added_compatible_warning() -> None:
    old = _snap(_adv(flags={"-fshort-enums"}))
    new = _snap(_adv(flags={"-fshort-enums", "-mabi=lp64"}))
    r = compare(old, new)
    kinds = {c.kind for c in r.changes}
    assert ChangeKind.TOOLCHAIN_FLAG_DRIFT in kinds
    # informational only — must NOT be BREAKING
    assert r.verdict != Verdict.BREAKING


def test_toolchain_flag_removed() -> None:
    results = diff_advanced_dwarf(
        _adv(flags={"-fshort-enums", "-fno-common"}),
        _adv(flags={"-fshort-enums"}),
    )
    flag_r = [r for r in results if r[0] == "toolchain_flag_drift"]
    assert len(flag_r) == 1
    assert "removed" in flag_r[0][2]


def test_toolchain_no_drift_no_change() -> None:
    results = diff_advanced_dwarf(
        _adv(flags={"-fshort-enums"}),
        _adv(flags={"-fshort-enums"}),
    )
    assert not any(r[0] == "toolchain_flag_drift" for r in results)


# ── DW_AT_producer parsing ────────────────────────────────────────────────────


def test_parse_producer_gcc() -> None:
    info = _parse_producer(
        "GNU C17 13.2.1 20230812 -fshort-enums -m64 -fabi-version=18"
    )
    assert info.compiler == "GCC"
    assert info.version == "13.2.1"
    assert "-fshort-enums" in info.abi_flags
    assert "-m64" in info.abi_flags
    assert "-fabi-version=18" in info.abi_flags


def test_parse_producer_clang() -> None:
    info = _parse_producer("clang version 17.0.0 -fpack-struct=4")
    assert info.compiler == "clang"
    assert "-fpack-struct=4" in info.abi_flags


def test_parse_producer_icc() -> None:
    info = _parse_producer("Intel(R) oneAPI DPC++/C++ Compiler 2024.0.0 -m64")
    assert info.compiler == "ICC"
    assert "-m64" in info.abi_flags


def test_parse_producer_cxx11abi() -> None:
    info = _parse_producer("GNU C++17 12.3 -D_GLIBCXX_USE_CXX11_ABI=0")
    assert "-D_GLIBCXX_USE_CXX11_ABI=0" in info.abi_flags


def test_parse_producer_no_flags() -> None:
    info = _parse_producer("GNU C17 11.4.0")
    assert info.compiler == "GCC"
    assert info.abi_flags == set()


def test_process_cu_unions_flags_across_cus() -> None:
    """G23-C: an ABI flag applied to only one CU is not missed (flags are
    unioned across all CUs, not taken from the first CU alone)."""
    from unittest.mock import MagicMock

    from abicheck.dwarf_advanced import AdvancedDwarfMetadata, _process_cu

    def _cu(producer: str):
        cu = MagicMock()
        top = MagicMock()
        top.attributes = {"DW_AT_producer": MagicMock(value=producer.encode())}
        top.iter_children.return_value = []
        cu.get_top_DIE.return_value = top
        return cu

    meta = AdvancedDwarfMetadata()
    _process_cu(_cu("GNU C17 13.2 -m64"), meta)
    _process_cu(_cu("GNU C17 13.2 -fshort-enums"), meta)
    # First CU sets identity; both CUs' flags are present.
    assert meta.toolchain.version == "13.2"
    assert "-m64" in meta.toolchain.abi_flags
    assert "-fshort-enums" in meta.toolchain.abi_flags


# ── JSON serialization (set → list → set roundtrip) ──────────────────────────


def test_serialization_roundtrip_no_crash() -> None:
    """snapshot_to_json must not raise TypeError on set fields."""
    from abicheck.storage.sectioned_document import from_sectioned_document

    snap = _snap(
        _adv(calling={"foo": "program"}, packed={"A", "B"}, flags={"-fshort-enums"})
    )
    # This must not raise TypeError: Object of type set is not JSON serializable
    json_str = snapshot_to_json(snap)
    data = from_sectioned_document(json.loads(json_str))
    assert isinstance(data["dwarf_advanced"]["packed_structs"], list)
    assert isinstance(data["dwarf_advanced"]["toolchain"]["abi_flags"], list)


def test_serialization_roundtrip_set_values() -> None:
    snap = _snap(
        _adv(calling={"foo": "program"}, packed={"A", "B"}, flags={"-fshort-enums"})
    )
    d = snapshot_to_dict(snap)
    snap2 = snapshot_from_dict(d)
    assert snap2.dwarf_advanced is not None
    assert snap2.dwarf_advanced.calling_conventions == {"foo": "program"}
    assert snap2.dwarf_advanced.packed_structs == {"A", "B"}
    assert snap2.dwarf_advanced.toolchain.abi_flags == {"-fshort-enums"}


def test_serialization_empty_sets_roundtrip() -> None:
    from abicheck.storage.sectioned_document import from_sectioned_document

    snap = _snap(_adv())
    json_str = snapshot_to_json(snap)
    data = from_sectioned_document(json.loads(json_str))
    assert data["dwarf_advanced"]["packed_structs"] == []


# ── Integration: real packed struct detection via DWARF ───────────────────────


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="ELF/DWARF tests require Linux")
def test_packed_struct_detected_from_real_dwarf() -> None:
    """Compile a packed struct with gcc -g and verify DWARF detection."""
    src = """
typedef struct __attribute__((packed)) {
    char a;
    int b;       /* misaligned: offset 1 (int needs align 4) */
    double c;    /* misaligned: offset 5 */
} PackedCtx;
PackedCtx g_ctx;
"""
    with tempfile.TemporaryDirectory() as td:
        so = Path(td) / "libpacked.so"
        result = subprocess.run(
            ["gcc", "-g", "-shared", "-fPIC", "-o", str(so), "-x", "c", "-"],
            input=src.encode(),
            capture_output=True,
        )
        if result.returncode != 0:
            pytest.skip(f"gcc failed: {result.stderr.decode()[:200]}")

        meta = parse_advanced_dwarf(so)

    assert meta.has_dwarf
    assert "PackedCtx" in meta.packed_structs, (
        f"Expected 'PackedCtx' in packed_structs, got: {meta.packed_structs}"
    )
    # P2 review: this standalone, still-public entry point must stamp
    # evidence_state on a clean parse, not leave it at the "not_available"
    # dataclass default.
    assert meta.evidence_state == "parsed"
    assert meta.cu_total >= 1
    assert meta.cu_failed == 0


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="ELF/DWARF tests require Linux")
def test_standard_struct_not_flagged_as_packed() -> None:
    """Standard-layout struct must NOT be flagged as packed."""
    src = """
typedef struct { int x; int y; double z; } NormalCtx;
NormalCtx g;
"""
    with tempfile.TemporaryDirectory() as td:
        so = Path(td) / "libnormal.so"
        result = subprocess.run(
            ["gcc", "-g", "-shared", "-fPIC", "-o", str(so), "-x", "c", "-"],
            input=src.encode(),
            capture_output=True,
        )
        if result.returncode != 0:
            pytest.skip(f"gcc failed: {result.stderr.decode()[:200]}")

        meta = parse_advanced_dwarf(so)

    assert meta.has_dwarf
    assert "NormalCtx" not in meta.packed_structs


# ── C3: compare()-level no-change test for value_abi_traits ──────────────────


def test_value_abi_traits_same_no_change_emitted() -> None:
    """Same value_abi_traits in both snapshots must NOT emit VALUE_ABI_TRAIT_CHANGED."""
    trait = "ret:trivial|p0:nontrivial"
    old = _snap(_adv(value_traits={"_Z6computeP3Foo": trait}))
    new = _snap(_adv(value_traits={"_Z6computeP3Foo": trait}))
    r = compare(old, new)
    kinds = {c.kind for c in r.changes}
    assert ChangeKind.VALUE_ABI_TRAIT_CHANGED not in kinds
    assert r.verdict == Verdict.NO_CHANGE


def test_value_abi_traits_changed_emits_change() -> None:
    """Different value_abi_traits for same symbol → a value-ABI finding emitted.

    A return-position flip is the struct-return-convention refinement; a
    parameter-position flip stays the generic value-ABI trait change.
    """
    old = _snap(_adv(value_traits={"_Z6computev": "ret:trivial"}))
    new = _snap(_adv(value_traits={"_Z6computev": "ret:nontrivial"}))
    r = compare(old, new)
    kinds = {c.kind for c in r.changes}
    assert ChangeKind.STRUCT_RETURN_CONVENTION_CHANGED in kinds

    old_p = _snap(_adv(value_traits={"_Z3fooP1S": "ret:trivial|p0:trivial"}))
    new_p = _snap(_adv(value_traits={"_Z3fooP1S": "ret:trivial|p0:nontrivial"}))
    r_p = compare(old_p, new_p)
    assert ChangeKind.VALUE_ABI_TRAIT_CHANGED in {c.kind for c in r_p.changes}
