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


def _struct_with_member_missing_type() -> _Die:
    """A named struct member that carries no DW_AT_type attribute at all --
    reaches _resolve_type's own `"DW_AT_type" not in die.attributes` branch,
    which has no reference to resolve (unlike the malformed-reference repro
    above) and so never reaches _resolve_type's except-branch accounting."""
    member = _Die("DW_TAG_member", {"DW_AT_name": "field"})
    return _Die(
        "DW_TAG_structure_type",
        {"DW_AT_name": "NoType", "DW_AT_byte_size": 4},
        children=[member],
    )


def _struct_with_anonymous_member_missing_type() -> _Die:
    """An anonymous DW_TAG_member with no DW_AT_type at all -- reaches
    _expand_anonymous_member's own `"DW_AT_type" not in die.attributes`
    branch, which has no reference to resolve (unlike the malformed-
    reference repro below) and so never reaches its except-branch
    accounting -- the anonymous-member sibling of
    _struct_with_member_missing_type above."""
    anon_member = _Die("DW_TAG_member", {})
    return _Die(
        "DW_TAG_structure_type",
        {"DW_AT_name": "OuterNoType", "DW_AT_byte_size": 8},
        children=[anon_member],
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

    def test_member_missing_type_marks_partial(self) -> None:
        """P2 review, fresh evidence (Codex): a named member DIE with no
        DW_AT_type at all (truncated/malformed debug info, not a
        legitimate type-less case -- a real struct member always carries
        DW_AT_type) previously fell through _resolve_type's own
        `"DW_AT_type" not in die.attributes` branch with no completeness
        signal, since that branch has no reference to resolve and so
        never reaches the except-branch accounting the sibling
        malformed-reference case above already fixed."""
        result = _run_parse([_struct_with_member_missing_type()])
        assert result.cu_failed == 0
        assert result.evidence_state == "partial"
        assert result.structs["NoType"].fields[0].type_name == "unknown"

    def test_anonymous_member_missing_type_marks_partial(self) -> None:
        """P2 review, fresh evidence (Codex): the anonymous-member sibling
        of test_member_missing_type_marks_partial above --
        _expand_anonymous_member's own no-DW_AT_type branch had the
        identical gap, silently dropping the anonymous member's nested
        layout with no completeness signal."""
        result = _run_parse([_struct_with_anonymous_member_missing_type()])
        assert result.cu_failed == 0
        assert result.evidence_state == "partial"
        # The anonymous member contributed no fields (silently dropped).
        assert result.structs["OuterNoType"].fields == []

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

    def test_cyclic_pointer_chain_marks_partial(self) -> None:
        """P2 review, fresh evidence (Codex): a cyclic type chain
        (ptrA -> ptrB -> ptrA -> ...) can never resolve via
        _resolve_ref/the memoisation cache alone -- each recursive step
        writes to the cache only *after* it returns, so the same
        (CU, offset) key is never seen mid-cycle. _die_to_type_info's own
        `depth > 8` guard is the only thing that stops this from recursing
        forever, substituting ("...", 0) once depth is exhausted -- but
        previously did so without touching the completeness accumulator,
        unlike every other placeholder-substitution site in this same call
        chain (an unresolved DW_AT_type reference, an out-of-range ref)."""
        member = _Die(
            "DW_TAG_member",
            {"DW_AT_name": "p", "DW_AT_type": _Attr(60, "DW_FORM_ref_addr")},
        )
        struct = _Die(
            "DW_TAG_structure_type",
            {"DW_AT_name": "Cyclic", "DW_AT_byte_size": 8},
            children=[member],
        )
        ptr_a = _Die(
            "DW_TAG_pointer_type",
            {"DW_AT_type": _Attr(61, "DW_FORM_ref_addr"), "DW_AT_byte_size": 8},
            offset=60,
        )
        ptr_b = _Die(
            "DW_TAG_pointer_type",
            {"DW_AT_type": _Attr(60, "DW_FORM_ref_addr"), "DW_AT_byte_size": 8},
            offset=61,
        )
        die_map = {60: ptr_a, 61: ptr_b}

        result = _run_parse([struct], die_map)
        assert result.cu_failed == 0
        assert result.evidence_state == "partial"
        # Best-effort output still emitted (the cycle guard's own
        # placeholder, wrapped in one "*" per pointer level unwound before
        # the guard fired), not dropped entirely.
        assert result.structs["Cyclic"].fields[0].type_name.startswith("...")

    def test_deep_acyclic_chain_marks_partial(self) -> None:
        """Sibling shape: a genuinely (not cyclic) more-than-nine-level
        pointer chain also exhausts the same depth guard."""
        depth_levels = 12
        die_map: dict[int, _Die] = {}
        base = _Die(
            "DW_TAG_base_type", {"DW_AT_name": "int", "DW_AT_byte_size": 4}, offset=100
        )
        die_map[100] = base
        next_offset = 100
        for i in range(depth_levels):
            this_offset = 200 + i
            die_map[this_offset] = _Die(
                "DW_TAG_pointer_type",
                {
                    "DW_AT_type": _Attr(next_offset, "DW_FORM_ref_addr"),
                    "DW_AT_byte_size": 8,
                },
                offset=this_offset,
            )
            next_offset = this_offset
        member = _Die(
            "DW_TAG_member",
            {"DW_AT_name": "p", "DW_AT_type": _Attr(next_offset, "DW_FORM_ref_addr")},
        )
        struct = _Die(
            "DW_TAG_structure_type",
            {"DW_AT_name": "Deep", "DW_AT_byte_size": 8},
            children=[member],
        )

        result = _run_parse([struct], die_map)
        assert result.cu_failed == 0
        assert result.evidence_state == "partial"

    def test_unsupported_tag_fallback_marks_partial(self) -> None:
        """P1 review, fresh evidence (Codex): a standard tag with no
        dedicated _compute_type_info() branch (e.g.
        DW_TAG_ptr_to_member_type, which typically carries no
        DW_AT_name) falls through to _compute_fallback_type_info(), which
        previously substituted a name/tag placeholder the same way an
        unresolved DW_AT_type reference does -- but without touching the
        completeness accumulator. Two DIEs sharing this fallback (e.g.
        `int A::*` vs `long A::*`, both bare DW_TAG_ptr_to_member_type
        with no name) would resolve to the identical placeholder string
        on both sides, reading as NO_CHANGE with evidence_state=parsed,
        silently masking a real field-type change."""
        member = _Die(
            "DW_TAG_member",
            {"DW_AT_name": "field", "DW_AT_type": _Attr(70, "DW_FORM_ref_addr")},
        )
        struct = _Die(
            "DW_TAG_structure_type",
            {"DW_AT_name": "HasPtrToMember", "DW_AT_byte_size": 8},
            children=[member],
        )
        # No DW_AT_name -- the exact shape that falls through to the
        # tag-string placeholder rather than a real type name.
        ptr_to_member = _Die(
            "DW_TAG_ptr_to_member_type",
            {"DW_AT_byte_size": 8},
            offset=70,
        )
        die_map = {70: ptr_to_member}

        result = _run_parse([struct], die_map)
        assert result.cu_failed == 0
        assert result.evidence_state == "partial"
        # Best-effort output still emitted (the tag itself, since there is
        # no DW_AT_name), not dropped entirely.
        assert (
            result.structs["HasPtrToMember"].fields[0].type_name
            == "DW_TAG_ptr_to_member_type"
        )

    def test_unsupported_tag_with_name_is_still_flagged(self) -> None:
        """P2 review, fresh evidence (Codex): this scenario was previously
        this module's own positive control ("a named fallback-shape tag
        returns real information, so must not be flagged") -- but a
        named tag reaching _compute_fallback_type_info still means this
        module has no dedicated understanding of that tag's own type
        semantics (only a raw best-effort name/size pair), and a real
        GCC-compiled `std::nullptr_t` field (this exact
        DW_TAG_unspecified_type/"decltype(nullptr)" shape) often carries
        no DW_AT_byte_size at all -- indistinguishable from this fixture's
        own explicit byte_size=8 via _attr_int's absent-vs-zero ambiguity.
        Every DIE reaching this fallback is now flagged, named or not."""
        member = _Die(
            "DW_TAG_member",
            {"DW_AT_name": "field", "DW_AT_type": _Attr(71, "DW_FORM_ref_addr")},
        )
        struct = _Die(
            "DW_TAG_structure_type",
            {"DW_AT_name": "HasNamedFallback", "DW_AT_byte_size": 8},
            children=[member],
        )
        named_fallback = _Die(
            "DW_TAG_unspecified_type",
            {"DW_AT_name": "decltype(nullptr)", "DW_AT_byte_size": 8},
            offset=71,
        )
        die_map = {71: named_fallback}

        result = _run_parse([struct], die_map)
        assert result.cu_failed == 0
        assert result.evidence_state == "partial"
        assert (
            result.structs["HasNamedFallback"].fields[0].type_name
            == "decltype(nullptr)"
        )

    def test_named_fallback_with_no_byte_size_marks_partial(self) -> None:
        """The exact scenario the reviewer named: a real GCC
        `std::nullptr_t` field's DW_TAG_unspecified_type genuinely carries
        no DW_AT_byte_size attribute at all (not merely a zero value) --
        this must be flagged the same as the fixture-supplied byte_size=8
        case above, since _attr_int can't tell the two apart and both
        reach the identical no-dedicated-branch fallback."""
        member = _Die(
            "DW_TAG_member",
            {"DW_AT_name": "field", "DW_AT_type": _Attr(72, "DW_FORM_ref_addr")},
        )
        struct = _Die(
            "DW_TAG_structure_type",
            {"DW_AT_name": "HasNullptrT", "DW_AT_byte_size": 8},
            children=[member],
        )
        # No DW_AT_byte_size at all -- the real-world GCC shape.
        named_fallback = _Die(
            "DW_TAG_unspecified_type",
            {"DW_AT_name": "decltype(nullptr)"},
            offset=72,
        )
        die_map = {72: named_fallback}

        result = _run_parse([struct], die_map)
        assert result.cu_failed == 0
        assert result.evidence_state == "partial"
        field = result.structs["HasNullptrT"].fields[0]
        assert field.type_name == "decltype(nullptr)"
        assert field.byte_size == 0


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

    def test_member_missing_type_marks_meta_partial(self) -> None:
        sess = _session_with([_struct_with_member_missing_type()])
        with patch("abicheck.dwarf_unified._parse_frame_registers", return_value=True):
            meta, _adv = parse_dwarf_from_session(sess)
        assert meta.cu_failed == 0
        assert meta.evidence_state == "partial"
        assert meta.structs["NoType"].fields[0].type_name == "unknown"

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
