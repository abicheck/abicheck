"""Branch-level tests of the initializer-byte reader, on synthetic ELF fragments.

The integration tests build real objects; these cover the guard branches a real
compiler rarely produces (an unallocated or truncated section, an offset past the
section end, a short read), against a fake ELF with the same fields the reader uses.
Pure Python, so they run on every platform.
"""

from __future__ import annotations

import io
from types import SimpleNamespace

from abicheck.extract.elf_data_initializer import (
    _covered_by_relocation,
    _is_defined_object,
    _symbol_bytes,
)

_ALLOC = 0x2
_WRITE = 0x1


class _FakeElf:
    elfclass = 64

    def __init__(self, sections: dict[int, dict[str, int | str]]) -> None:
        self._sections = sections

    def get_section(self, index: int):
        return self._sections.get(index)


def _obj(shndx: int, value: int, size: int, *, typ: str = "STT_OBJECT"):
    entry = {
        "st_info": {"type": typ},
        "st_shndx": shndx,
        "st_value": value,
        "st_size": size,
    }
    return SimpleNamespace(entry=entry)


def _section(
    *,
    flags: int = _ALLOC,
    typ: str = "SHT_PROGBITS",
    addr: int = 0x1000,
    offset: int = 0,
    size: int = 16,
) -> dict[str, int | str]:
    return {
        "sh_type": typ,
        "sh_flags": flags,
        "sh_addr": addr,
        "sh_offset": offset,
        "sh_size": size,
    }


_CONTENT = bytes(range(64))


def _read(sections, sym, relocated=()):
    return _symbol_bytes(io.BytesIO(_CONTENT), _FakeElf(sections), sym, list(relocated))


def test_a_read_only_object_in_an_allocated_section_is_read() -> None:
    raw = _read({2: _section()}, _obj(2, 0x1004, 4))
    assert raw == _CONTENT[4:8]


def test_an_object_outside_the_size_bounds_is_not_read() -> None:
    assert _read({2: _section()}, _obj(2, 0x1000, 0)) is None
    assert _read({2: _section()}, _obj(2, 0x1000, 4097)) is None


def test_a_missing_section_is_not_read() -> None:
    assert _read({}, _obj(7, 0x1000, 4)) is None


def test_a_no_bits_section_is_not_read() -> None:
    assert _read({2: _section(typ="SHT_NOBITS")}, _obj(2, 0x1000, 4)) is None


def test_a_writable_section_is_not_read() -> None:
    assert _read({2: _section(flags=_ALLOC | _WRITE)}, _obj(2, 0x1000, 4)) is None


def test_an_unallocated_section_is_not_read() -> None:
    assert _read({2: _section(flags=0)}, _obj(2, 0x1000, 4)) is None


def test_an_object_past_the_section_end_is_not_read() -> None:
    # Starts before the section, and ends past it.
    assert _read({2: _section(addr=0x1000, size=16)}, _obj(2, 0x0FFC, 8)) is None
    assert _read({2: _section(addr=0x1000, size=16)}, _obj(2, 0x100C, 8)) is None


def test_a_truncated_file_is_not_read() -> None:
    # The section claims 16 bytes at offset 60 of a 64-byte file: only 4 are there.
    sections = {2: _section(offset=60, size=16)}
    assert _read(sections, _obj(2, 0x1000, 8)) is None


def test_a_relocated_object_is_not_read() -> None:
    assert _read({2: _section()}, _obj(2, 0x1004, 4), relocated=[0x1006]) is None


def test_only_a_defined_object_symbol_is_defined() -> None:
    assert _is_defined_object(_obj(2, 0x1000, 4))
    assert not _is_defined_object(_obj(2, 0x1000, 4, typ="STT_FUNC"))
    assert not _is_defined_object(_obj(0, 0x1000, 4))  # SHN_UNDEF


def test_a_relocation_at_unit_zero_covers_the_object_at_zero() -> None:
    assert _covered_by_relocation([0], 0, 8, 8)
