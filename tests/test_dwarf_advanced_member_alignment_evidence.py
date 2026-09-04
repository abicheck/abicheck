"""DWARF advanced-channel per-member completeness tests: packing detection
(_check_packed / _get_type_align) and qualifier-unwrap forwarding.

Split out of test_sprint4_dwarf_advanced.py once that file grew past the
architecture gate's 1200-line test-file cap (mirrors
test_mutation_per_module_scoping.py's own split from
test_mutation_run_scoping.py for the identical reason -- see tests/CLAUDE.md).
These classes are topically cohesive: both cover a member DIE's own
DW_AT_type resolution feeding _check_packed's per-struct packing verdict,
distinct from the CU-/typedef-level completeness classes that remain in the
parent file.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from abicheck.dwarf_advanced import parse_advanced_dwarf
from tests.test_dwarf_metadata_coverage import _CU, _Attr, _Die


class TestPackedMemberIncompletePropagation:
    """P1 review, fresh evidence (Codex): distinct from
    TestPackedTypedefIncompletePropagation above (a malformed typedef
    *target*), this is a member *inside* an already-resolved, named struct
    whose own DW_AT_type is unresolvable. That failure is caught deep
    inside _get_type_align (called from _check_packed, reached from
    _walk_cu's direct DW_TAG_structure_type branch, not
    _check_packed_typedef) and previously returned 0 (the same value a
    legitimate composite-type skip returns) with no completeness signal at
    all -- so a packing change hiding behind that one bad member was
    silently missed under --require-complete-analysis while
    evidence_state still reported "parsed"."""

    def test_malformed_member_type_marks_partial(self) -> None:
        bad_member = _Die(
            "DW_TAG_member",
            {"DW_AT_name": "x", "DW_AT_type": _Attr(999, "DW_FORM_ref_addr")},
        )
        struct = _Die(
            "DW_TAG_structure_type",
            {"DW_AT_name": "Named", "DW_AT_byte_size": 4},
            children=[bad_member],
        )
        root = _Die("DW_TAG_compile_unit", children=[struct])
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
        # The struct itself was seen (registered by name) but its one
        # member's type never resolved, so no packing verdict for it can
        # be trusted -- it must not be silently reported "not packed".
        assert "Named" in meta.all_struct_names
        assert "Named" not in meta.packed_structs

    def test_member_missing_type_marks_partial(self) -> None:
        """P2 review, fresh evidence (Codex): distinct from
        test_malformed_member_type_marks_partial above (a member whose
        DW_AT_type reference exists but is itself unresolvable) -- here
        the member DIE carries no DW_AT_type attribute at all, truncated/
        malformed debug info rather than a legitimate type-less case.
        _get_type_align's own "DW_AT_type not in attributes" branch
        previously returned 0 (the same value a legitimate composite-type
        skip returns) with no completeness signal, the advanced-channel
        sibling of the identical basic-channel gap dwarf_metadata.py's
        _process_member already fixed."""
        member_no_type = _Die("DW_TAG_member", {"DW_AT_name": "x"})
        struct = _Die(
            "DW_TAG_structure_type",
            {"DW_AT_name": "Named", "DW_AT_byte_size": 4},
            children=[member_no_type],
        )
        root = _Die("DW_TAG_compile_unit", children=[struct])
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
        ):
            meta = parse_advanced_dwarf(Path(__file__))

        assert meta.cu_failed == 0
        assert meta.evidence_state == "partial"
        assert "Named" in meta.all_struct_names
        assert "Named" not in meta.packed_structs

    def test_resolvable_misaligned_member_is_not_flagged(self) -> None:
        """Positive control: a fully-resolvable named struct with a
        genuinely misaligned field must not be flagged, and its packing
        must still be recorded correctly -- proving the fix didn't disturb
        the ordinary packed-struct detection path."""
        char_member = _Die(
            "DW_TAG_member",
            {"DW_AT_name": "c", "DW_AT_type": _Attr(20, "DW_FORM_ref_addr")},
        )
        int_member = _Die(
            "DW_TAG_member",
            {
                "DW_AT_name": "i",
                "DW_AT_type": _Attr(21, "DW_FORM_ref_addr"),
                "DW_AT_data_member_location": _Attr(1),
            },
        )
        struct = _Die(
            "DW_TAG_structure_type",
            {"DW_AT_name": "Named", "DW_AT_byte_size": 5},
            children=[char_member, int_member],
        )
        root = _Die("DW_TAG_compile_unit", children=[struct])
        cu = _CU(top_die=root, offset=0)
        char_type = _Die(
            "DW_TAG_base_type", {"DW_AT_name": "char", "DW_AT_byte_size": 1}, offset=20
        )
        int_type = _Die(
            "DW_TAG_base_type", {"DW_AT_name": "int", "DW_AT_byte_size": 4}, offset=21
        )
        cu._die_map = {20: char_type, 21: int_type}

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
        assert "Named" in meta.packed_structs


class TestGetTypeAlignForwardsIncompleteThroughQualifierUnwrap:
    """P1 review, fresh evidence (Codex): distinct from
    TestPackedMemberIncompletePropagation above (the member's own
    DW_AT_type reference is itself unresolvable). Here the member's
    DW_AT_type resolves fine to a typedef, but *that* typedef's own
    DW_AT_type is unresolvable -- `_get_type_align` unwraps qualifiers via
    `_unwrap_qualifiers`, which has its own `incomplete` accumulator (used
    by `_resolve_type_die` inside it), but `_get_type_align` previously
    called it without forwarding its own `incomplete` parameter, so the
    inner failure was swallowed and `_unwrap_qualifiers` returned the
    still-unresolved typedef DIE with no completeness signal at all --
    `_get_type_align` then fell through to its composite-type branch and
    returned a silent 0, exactly like a legitimate skip, while
    evidence_state still reported "parsed"."""

    def test_unresolvable_typedef_target_marks_partial(self) -> None:
        # member -> typedef (resolves fine) -> unresolvable inner type
        typedef_die = _Die(
            "DW_TAG_typedef",
            {
                "DW_AT_name": "opaque_t",
                "DW_AT_type": _Attr(999, "DW_FORM_ref_addr"),
            },
            offset=30,
        )
        bad_member = _Die(
            "DW_TAG_member",
            {"DW_AT_name": "x", "DW_AT_type": _Attr(30, "DW_FORM_ref_addr")},
        )
        struct = _Die(
            "DW_TAG_structure_type",
            {"DW_AT_name": "Named", "DW_AT_byte_size": 4},
            children=[bad_member],
        )
        root = _Die("DW_TAG_compile_unit", children=[struct])
        cu = _CU(top_die=root, offset=0)
        # The member's own DW_AT_type (offset 30) resolves normally; only
        # the typedef's inner DW_AT_type (offset 999) is missing.
        cu._die_map = {30: typedef_die}

        mock_elf = MagicMock()
        mock_dwarf = MagicMock()
        mock_dwarf.iter_CUs.return_value = [cu]
        mock_elf.get_dwarf_info.return_value = mock_dwarf

        with (
            patch("abicheck.dwarf_advanced.ELFFile", return_value=mock_elf),
            patch("abicheck.dwarf_advanced.has_real_dwarf_info", return_value=True),
            patch("abicheck.dwarf_advanced._parse_frame_registers", return_value=True),
            # Only the *typedef's own* inner resolution goes through
            # dwarf_utils.resolve_type_die -> dwarf_utils.resolve_die_ref;
            # the member's own outer DW_AT_type resolution in
            # _get_type_align uses a separately-imported name binding
            # (abicheck.dwarf_advanced._resolve_die_ref) and is unaffected
            # by this patch, so it still resolves the typedef DIE normally
            # via cu._die_map.
            patch(
                "abicheck.dwarf_utils.resolve_die_ref",
                side_effect=RuntimeError("bad ref"),
            ),
        ):
            meta = parse_advanced_dwarf(Path(__file__))

        assert meta.cu_failed == 0
        assert meta.evidence_state == "partial"
        assert "Named" in meta.all_struct_names
        assert "Named" not in meta.packed_structs

    def test_resolvable_typedef_target_is_not_flagged(self) -> None:
        """Positive control: a fully-resolvable typedef chain must not be
        flagged, proving the fix didn't disturb ordinary qualifier
        unwrapping."""
        int_type = _Die(
            "DW_TAG_base_type",
            {"DW_AT_name": "int", "DW_AT_byte_size": 4},
            offset=21,
        )
        typedef_die = _Die(
            "DW_TAG_typedef",
            {"DW_AT_name": "myint_t", "DW_AT_type": _Attr(21, "DW_FORM_ref_addr")},
            offset=30,
        )
        member = _Die(
            "DW_TAG_member",
            {"DW_AT_name": "x", "DW_AT_type": _Attr(30, "DW_FORM_ref_addr")},
        )
        struct = _Die(
            "DW_TAG_structure_type",
            {"DW_AT_name": "Named", "DW_AT_byte_size": 4},
            children=[member],
        )
        root = _Die("DW_TAG_compile_unit", children=[struct])
        cu = _CU(top_die=root, offset=0)
        cu._die_map = {30: typedef_die, 21: int_type}

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
        assert "Named" in meta.all_struct_names
