"""Regression tests: a swallowed per-DIE type-resolution failure inside an
otherwise-successful DWARF CU must downgrade ``evidence_state`` to
``"partial"``, on both DWARF entry points (P1 review, `AGENTS.md`'s "bug
class, not one input" contract).

Before this fix, a malformed ``DW_AT_type`` reference caught by one of the
many ``except Exception:`` swallow points inside the type-resolution call
chain (``_resolve_type``, ``_process_typedef``, ``_expand_anonymous_member``,
``_resolve_inner_type_info``) returned a placeholder ("unknown"/None) without
recording anything -- the CU-level ``try/except`` around ``_process_cu`` only
ever sees an exception that escaped *every one* of those inner catches, so
``cu_failed`` stayed 0 and a run could read back ``evidence_state="parsed"``
(and, with ``--require-complete-analysis``, exit 0) despite silently losing a
field/typedef/nested-type fact. This exercises every one of those swallow
points through the *public* parser entry points
(``abicheck.dwarf_metadata.parse_dwarf_metadata`` / its internal ``_parse``,
and ``abicheck.dwarf_unified.parse_dwarf_from_session``) rather than only at
the helper level, per the reviewer's explicit instruction, plus a positive
control proving a clean parse is not flagged.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from abicheck.dwarf_metadata import _parse
from abicheck.dwarf_unified import DwarfSession, parse_dwarf_from_session
from tests.test_dwarf_metadata_coverage import _CU, _Attr, _Die

# ---------------------------------------------------------------------------
# Sibling malformed-DW_AT_type repros, each through a different swallow point
# ---------------------------------------------------------------------------


def _struct_with_bad_member_type() -> _Die:
    """A struct whose one member's DW_AT_type references an offset the CU
    never resolves -- reaches _process_member -> _resolve_type's own
    except Exception: return ("unknown", 0)."""
    member = _Die(
        "DW_TAG_member",
        {"DW_AT_name": "field", "DW_AT_type": _Attr(999, "DW_FORM_ref_addr")},
    )
    return _Die(
        "DW_TAG_structure_type",
        {"DW_AT_name": "S", "DW_AT_byte_size": 4},
        children=[member],
    )


def _typedef_with_bad_target() -> _Die:
    """A typedef whose DW_AT_type references an unresolved offset -- reaches
    _process_typedef's own except Exception: incomplete.append(True); return."""
    return _Die(
        "DW_TAG_typedef",
        {"DW_AT_name": "MyAlias", "DW_AT_type": _Attr(999, "DW_FORM_ref_addr")},
    )


def _struct_with_bad_anonymous_member() -> _Die:
    """A struct whose one *anonymous* member's DW_AT_type references an
    unresolved offset -- reaches _expand_anonymous_member's own
    except Exception: incomplete.append(True); return []."""
    anon_member = _Die("DW_TAG_member", {"DW_AT_type": _Attr(999, "DW_FORM_ref_addr")})
    return _Die(
        "DW_TAG_structure_type",
        {"DW_AT_name": "Outer", "DW_AT_byte_size": 8},
        children=[anon_member],
    )


def _struct_with_bad_nested_pointer_type() -> _Die:
    """A struct member typed as ``const T*`` where the pointee ``T``'s own
    DW_AT_type is unresolved -- reaches _resolve_inner_type_info's own
    except Exception: incomplete.append(True); return None, nested two levels
    deep under _compute_pointer_like_info -> _resolve_inner_type_name."""
    # const-qualified type whose own DW_AT_type is unresolved
    const_die = _Die(
        "DW_TAG_const_type",
        {"DW_AT_type": _Attr(999, "DW_FORM_ref_addr")},
        offset=50,
    )
    ptr = _Die(
        "DW_TAG_pointer_type",
        {"DW_AT_type": _Attr(50, "DW_FORM_ref_addr"), "DW_AT_byte_size": 8},
        offset=51,
    )
    member = _Die(
        "DW_TAG_member",
        {"DW_AT_name": "p", "DW_AT_type": _Attr(51, "DW_FORM_ref_addr")},
    )
    struct = _Die(
        "DW_TAG_structure_type",
        {"DW_AT_name": "HasPtr", "DW_AT_byte_size": 8},
        children=[member],
    )
    return struct, {50: const_die, 51: ptr}


def _clean_struct() -> tuple[_Die, dict[int, _Die]]:
    """A fully-resolvable struct -- the positive control."""
    base = _Die(
        "DW_TAG_base_type", {"DW_AT_name": "int", "DW_AT_byte_size": 4}, offset=10
    )
    member = _Die(
        "DW_TAG_member",
        {"DW_AT_name": "x", "DW_AT_type": _Attr(10, "DW_FORM_ref_addr")},
    )
    struct = _Die(
        "DW_TAG_structure_type",
        {"DW_AT_name": "Clean", "DW_AT_byte_size": 4},
        children=[member],
    )
    return struct, {10: base}


def _run_parse(top_children: list[_Die], die_map: dict[int, _Die] | None = None):
    """Drive the standalone parser (abicheck.dwarf_metadata._parse) over one
    synthetic CU whose top DIE has *top_children*."""
    root = _Die("DW_TAG_compile_unit", children=top_children)
    cu = _CU(top_die=root, offset=0)
    cu._die_map = die_map or {}

    mock_elf = MagicMock()
    mock_elf.has_dwarf_info.return_value = True
    mock_dwarf = MagicMock()
    mock_dwarf.iter_CUs.return_value = [cu]
    mock_elf.get_dwarf_info.return_value = mock_dwarf

    with patch("abicheck.dwarf_metadata.ELFFile", return_value=mock_elf):
        return _parse(MagicMock(), Path("/fake.so"))


class TestStandaloneParserFlagsIncompleteTypeResolution:
    """abicheck.dwarf_metadata.parse_dwarf_metadata / its internal _parse."""

    def test_malformed_struct_member_type_marks_partial(self) -> None:
        result = _run_parse([_struct_with_bad_member_type()])
        assert result.has_dwarf is True
        assert result.cu_total == 1
        assert result.cu_failed == 0  # the CU itself did not raise
        assert result.evidence_state == "partial"
        assert result.structs["S"].fields[0].type_name == "unknown"

    def test_malformed_typedef_target_marks_partial(self) -> None:
        # Real pyelftools raises (DWARFError) for an unresolvable ref rather
        # than returning None -- patch _resolve_ref to match that contract,
        # same as the existing TestProcessTypedef coverage tests do.
        with patch(
            "abicheck.dwarf_metadata._resolve_ref", side_effect=RuntimeError("bad")
        ):
            result = _run_parse([_typedef_with_bad_target()])
        assert result.cu_failed == 0
        assert result.evidence_state == "partial"

    def test_malformed_anonymous_member_type_marks_partial(self) -> None:
        with patch(
            "abicheck.dwarf_metadata._resolve_ref", side_effect=RuntimeError("bad")
        ):
            result = _run_parse([_struct_with_bad_anonymous_member()])
        assert result.cu_failed == 0
        assert result.evidence_state == "partial"
        # The anonymous member contributed no fields (silently dropped).
        assert result.structs["Outer"].fields == []

    def test_malformed_nested_pointer_qualifier_type_marks_partial(self) -> None:
        struct, die_map = _struct_with_bad_nested_pointer_type()
        result = _run_parse([struct], die_map)
        assert result.cu_failed == 0
        assert result.evidence_state == "partial"
        # The pointer itself resolves (byte_size known); only its pointee
        # (the const-qualified inner type) fell back to a placeholder.
        field = result.structs["HasPtr"].fields[0]
        assert field.type_name == "const *"

    def test_clean_parse_is_not_flagged_partial(self) -> None:
        """Positive control: a fully-resolvable parse must stay 'parsed'."""
        struct, die_map = _clean_struct()
        result = _run_parse([struct], die_map)
        assert result.cu_failed == 0
        assert result.evidence_state == "parsed"
        assert result.structs["Clean"].fields[0].type_name == "int"


# ---------------------------------------------------------------------------
# Unified single-pass entry point (dumper.py's real ELF-dump path)
# ---------------------------------------------------------------------------


def _session_with(top_children: list[_Die], die_map: dict[int, _Die] | None = None):
    root = _Die("DW_TAG_compile_unit", children=top_children)
    cu = _CU(top_die=root, offset=0)
    cu._die_map = die_map or {}

    class _DwarfInfo:
        def iter_CUs(self):
            return [cu]

    return DwarfSession(
        path=Path("libtest.so"),
        _file=object(),  # type: ignore[arg-type]
        elf=object(),
        dwarf=_DwarfInfo(),
        arch="x86_64",  # type: ignore[arg-type]
    )


class TestUnifiedPassFlagsIncompleteTypeResolution:
    """abicheck.dwarf_unified.parse_dwarf_from_session -- the path dumper.py's
    real ELF dumps actually use, named explicitly by the reviewer alongside
    the standalone parser above."""

    def test_malformed_struct_member_type_marks_meta_partial(self) -> None:
        sess = _session_with([_struct_with_bad_member_type()])
        with patch("abicheck.dwarf_unified._parse_frame_registers", return_value=True):
            meta, _adv = parse_dwarf_from_session(sess)
        assert meta.cu_total == 1
        assert meta.cu_failed == 0
        assert meta.evidence_state == "partial"
        assert meta.structs["S"].fields[0].type_name == "unknown"

    def test_clean_parse_is_not_flagged_partial(self) -> None:
        struct, die_map = _clean_struct()
        sess = _session_with([struct], die_map)
        with patch("abicheck.dwarf_unified._parse_frame_registers", return_value=True):
            meta, _adv = parse_dwarf_from_session(sess)
        assert meta.cu_failed == 0
        assert meta.evidence_state == "parsed"

    def test_incomplete_never_downgrades_an_already_failed_channel(self) -> None:
        """A CU-level failure already forces 'failed'/'partial' -- the new
        incomplete-type-resolution check must not fight that classification
        (it only ever downgrades a clean 'parsed', per its own guard)."""

        class _RaisingDwarfInfo:
            def iter_CUs(self):
                bad = MagicMock()
                bad.get_top_DIE.side_effect = RuntimeError("corrupt CU")
                return [bad]

        sess = DwarfSession(
            path=Path("libbad.so"),
            _file=object(),  # type: ignore[arg-type]
            elf=object(),
            dwarf=_RaisingDwarfInfo(),
            arch="x86_64",  # type: ignore[arg-type]
        )
        with patch("abicheck.dwarf_unified._parse_frame_registers", return_value=True):
            meta, _adv = parse_dwarf_from_session(sess)
        assert meta.cu_failed == 1
        assert meta.evidence_state == "failed"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
