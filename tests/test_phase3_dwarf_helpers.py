"""Phase 3 DWARF confidence tests for helper/edge-case behavior."""
from __future__ import annotations

from types import SimpleNamespace

from abicheck import dwarf_advanced as da
from abicheck import dwarf_metadata as dm


class _Attr:
    def __init__(self, value, form="DW_FORM_ref4"):
        self.value = value
        self.form = form


class _Die:
    def __init__(self, tag: str, attrs: dict[str, object] | None = None, children=None, offset: int = 0):
        self.tag = tag
        self.attributes = attrs or {}
        self._children = list(children or [])
        self.offset = offset

    def iter_children(self):
        return iter(self._children)


def test_dwarf_metadata_resolve_ref_handles_relative_and_absolute_forms():
    resolved = []

    class _CU:
        cu_offset = 100

        def get_DIE_from_refaddr(self, off):
            resolved.append(off)
            return f"die@{off}"

    cu = _CU()
    rel_die = _Die("X", {"DW_AT_type": _Attr(7, "DW_FORM_ref4")})
    abs_die = _Die("X", {"DW_AT_type": _Attr(77, "DW_FORM_ref_addr")})

    assert dm._resolve_ref(rel_die, "DW_AT_type", cu) == "die@107"
    assert dm._resolve_ref(abs_die, "DW_AT_type", cu) == "die@77"
    assert resolved == [107, 77]


def test_dwarf_metadata_die_to_type_info_depth_limit_and_cache():
    cu = SimpleNamespace(cu_offset=1)
    die = _Die("DW_TAG_base_type", {"DW_AT_name": _Attr("int"), "DW_AT_byte_size": _Attr(4)}, offset=9)
    cache: dict[tuple[int, int], tuple[str, int]] = {}

    assert dm._die_to_type_info(die, cu, depth=9, cache=cache) == ("...", 0)

    first = dm._die_to_type_info(die, cu, depth=0, cache=cache)
    second = dm._die_to_type_info(die, cu, depth=0, cache=cache)
    assert first == ("int", 4)
    assert second == ("int", 4)
    assert cache[(1, 9)] == ("int", 4)


def test_dwarf_metadata_compute_type_info_pointer_fallback_on_resolution_error(monkeypatch):
    die = _Die("DW_TAG_pointer_type", {"DW_AT_type": _Attr(3), "DW_AT_byte_size": _Attr(8)})
    monkeypatch.setattr(dm, "_resolve_ref", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bad")))

    out = dm._compute_type_info(die, SimpleNamespace(cu_offset=0), 0, {})
    assert out == ("void *", 8)


def test_dwarf_advanced_decode_member_location_forms():
    int_member = _Die("DW_TAG_member", {"DW_AT_data_member_location": _Attr(12)})
    # Shared decoder uses tuples (opcode, operand) instead of DWARFExprOp objects
    expr_member = _Die(
        "DW_TAG_member",
        {"DW_AT_data_member_location": _Attr([(0x23, 16)])},
    )
    bad_member = _Die(
        "DW_TAG_member",
        {"DW_AT_data_member_location": _Attr([(0x99, 0)])},
    )

    assert da._decode_member_location(int_member) == 12
    assert da._decode_member_location(expr_member) == 16
    assert da._decode_member_location(bad_member) == 0


def test_dwarf_advanced_get_type_align_follows_typedef_chain_and_alignment_attr():
    base = _Die("DW_TAG_base_type", {"DW_AT_alignment": _Attr(8), "DW_AT_byte_size": _Attr(8)}, offset=30)
    typedef = _Die("DW_TAG_typedef", {"DW_AT_type": _Attr(30, "DW_FORM_ref_addr")}, offset=20)
    member = _Die("DW_TAG_member", {"DW_AT_type": _Attr(20, "DW_FORM_ref_addr")})

    class _CU:
        cu_offset = 0

        def get_DIE_from_refaddr(self, off):
            return {20: typedef, 30: base}[off]

    assert da._get_type_align(member, _CU()) == 8


def test_dwarf_advanced_extract_calling_convention_external_and_unknown():
    meta = da.AdvancedDwarfMetadata(has_dwarf=True)
    hidden = _Die("DW_TAG_subprogram", {"DW_AT_external": _Attr(0), "DW_AT_name": _Attr("f")})
    da._extract_calling_convention(hidden, meta, CU=SimpleNamespace(cu_offset=0))
    assert meta.calling_conventions == {}

    exported = _Die(
        "DW_TAG_subprogram",
        {
            "DW_AT_external": _Attr(1),
            "DW_AT_linkage_name": _Attr("_Z3foov"),
            "DW_AT_calling_convention": _Attr(0xFE),
        },
    )
    da._extract_calling_convention(exported, meta, CU=SimpleNamespace(cu_offset=0))
    assert meta.calling_conventions["_Z3foov"] == "unknown(0xfe)"


def test_dwarf_advanced_check_packed_detects_misaligned_field(monkeypatch):
    member = _Die("DW_TAG_member", {"DW_AT_data_member_location": _Attr(2), "DW_AT_bit_size": _Attr(0)})
    struct_die = _Die("DW_TAG_structure_type", {"DW_AT_name": _Attr("S"), "DW_AT_byte_size": _Attr(8)}, [member])

    monkeypatch.setattr(da, "_get_type_align", lambda *_args, **_kwargs: 4)

    meta = da.AdvancedDwarfMetadata(has_dwarf=True)
    da._check_packed(struct_die, meta, CU=SimpleNamespace(cu_offset=0))

    assert "S" in meta.all_struct_names
    assert "S" in meta.packed_structs


# ---------------------------------------------------------------------------
# has_real_dwarf_info — strict DWARF detection (excludes .eh_frame)
# ---------------------------------------------------------------------------

class _FakeElf:
    """Minimal ELFFile stand-in exposing get_section_by_name."""

    def __init__(self, section_names: set[str]) -> None:
        self._sections = set(section_names)

    def get_section_by_name(self, name: str):
        return object() if name in self._sections else None


def test_has_real_dwarf_info_true_with_debug_info():
    from abicheck.dwarf_utils import has_real_dwarf_info
    assert has_real_dwarf_info(_FakeElf({".debug_info", ".eh_frame"})) is True


def test_has_real_dwarf_info_true_with_compressed_zdebug_info():
    from abicheck.dwarf_utils import has_real_dwarf_info
    assert has_real_dwarf_info(_FakeElf({".zdebug_info"})) is True


def test_has_real_dwarf_info_false_when_only_eh_frame():
    """A stripped binary keeps .eh_frame but no .debug_* — must read as no DWARF."""
    from abicheck.dwarf_utils import has_real_dwarf_info
    assert has_real_dwarf_info(_FakeElf({".eh_frame"})) is False


def test_has_real_dwarf_info_false_when_no_sections():
    from abicheck.dwarf_utils import has_real_dwarf_info
    assert has_real_dwarf_info(_FakeElf(set())) is False
