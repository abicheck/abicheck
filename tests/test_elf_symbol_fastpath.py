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

"""``iter_symbol_fields`` must decode exactly what pyelftools decodes.

**Bug class.** A fixed-layout fast path replaces a third-party parser for
the cases it claims to support. Every field it mis-strides, mis-masks or
mis-orders becomes a *silently different answer*, not a crash: a symbol
whose binding decodes wrong is quietly admitted to or dropped from the
export set, and the only signal is a compatibility verdict that changed
for no stated reason. ELF32 in particular orders the record differently
from ELF64 (``st_value``/``st_size`` precede ``st_info``/``st_other``),
so a format string copied between them decodes plausible-looking
garbage.

**General invariant**, and the reason these tests build their own ELF
bytes rather than using this host's compiler: for *every* supported
(class, endianness) combination and *every* field value, the fast path
agrees with pyelftools' own parse of the same bytes. The oracle is
pyelftools -- its `Elf_Sym` struct builds the entries and its
`SymbolTableSection.iter_symbols` reads them back -- never this module's
own format strings, which is the tautology ADR-059 §12 warns about. This
host emits only 64-bit little-endian ELF, so a test limited to what
`gcc` produces here would leave three of the four supported
combinations, including both big-endian ones and the differently-ordered
ELF32 record, entirely unexercised.

The refusal path gets the same treatment: a table this module declines
must return `None` (use the ordinary reader), never a wrong answer and
never an empty one, since "unsupported" and "no symbols" lead to opposite
outcomes in the caller.
"""

from __future__ import annotations

import io
import itertools

import pytest
from elftools.construct.lib import Container
from elftools.elf.enums import (
    ENUM_ST_INFO_BIND,
    ENUM_ST_INFO_TYPE,
    ENUM_ST_SHNDX,
    ENUM_ST_VISIBILITY,
)
from elftools.elf.sections import SymbolTableSection
from elftools.elf.structs import ELFStructs

from abicheck.extract.elf_symbol_fastpath import (
    SHN_ABS,
    SHN_UNDEF,
    STB_GLOBAL,
    STB_WEAK,
    STV_HIDDEN,
    STV_INTERNAL,
    iter_symbol_fields,
)

_VARIANTS = list(itertools.product((32, 64), (True, False)))
_VARIANT_IDS = [f"elf{c}-{'le' if le else 'be'}" for c, le in _VARIANTS]

_STRTAB_BLOB = b"\x00alpha\x00beta\x00gamma\x00"


class _StringTable:
    """Minimal stand-in for pyelftools' `StringTableSection`."""

    def __init__(self, blob: bytes = _STRTAB_BLOB) -> None:
        self.blob = blob

    def get_string(self, offset: int) -> str:
        end = self.blob.find(b"\x00", offset)
        if end < 0:
            return ""
        return self.blob[offset:end].decode("utf-8", "replace")


class _FakeELF:
    def __init__(self, structs, elfclass: int, little_endian: bool, stream) -> None:
        self.structs = structs
        self.elfclass = elfclass
        self.little_endian = little_endian
        self.stream = stream


def _structs(elfclass: int, little_endian: bool) -> ELFStructs:
    structs = ELFStructs(little_endian=little_endian, elfclass=elfclass)
    structs.create_basic_structs()
    structs.create_advanced_structs()
    return structs


def _table(elfclass, little_endian, entries, *, entsize=None, offset=0, pad=0):
    """A real `SymbolTableSection` over bytes built by pyelftools itself."""
    structs = _structs(elfclass, little_endian)
    blob = b"".join(structs.Elf_Sym.build(Container(**e)) for e in entries)
    stream = io.BytesIO(b"\x00" * offset + blob + b"\x00" * pad)
    elffile = _FakeELF(structs, elfclass, little_endian, stream)
    header = Container(
        sh_offset=offset,
        sh_size=len(blob),
        sh_entsize=structs.Elf_Sym.sizeof() if entsize is None else entsize,
        sh_type="SHT_SYMTAB",
        sh_flags=0,
        sh_addr=0,
        sh_link=0,
        sh_info=0,
        sh_addralign=1,
        sh_name=0,
    )
    section = SymbolTableSection(header, ".symtab", elffile, _StringTable())
    return elffile, section


def _entry(
    *,
    name=1,
    bind="STB_GLOBAL",
    sym_type="STT_FUNC",
    vis="STV_DEFAULT",
    shndx=3,
    value=0x10,
    size=4,
):
    return {
        "st_name": name,
        "st_value": value,
        "st_size": size,
        "st_info": Container(bind=bind, type=sym_type),
        "st_other": Container(visibility=vis, local=0),
        "st_shndx": shndx,
    }


def _oracle(section):
    """What pyelftools decodes, as the same 4-tuple shape.

    pyelftools hands back its *names* (`"STB_GLOBAL"`, `"SHN_UNDEF"`), so
    they are mapped forward through the very enums it used to produce
    them. A value with no name stays an integer, which is what it does
    for an ordinary section index.
    """
    out = []
    for sym in section.iter_symbols():
        e = sym.entry
        bind, vis, shndx = e.st_info.bind, e.st_other.visibility, e.st_shndx
        out.append(
            (
                e.st_name,
                ENUM_ST_INFO_BIND[bind] if isinstance(bind, str) else bind,
                ENUM_ST_VISIBILITY[vis] if isinstance(vis, str) else vis,
                ENUM_ST_SHNDX[shndx] if isinstance(shndx, str) else shndx,
            )
        )
    return out


class TestAgreesWithPyelftools:
    @pytest.mark.parametrize(("elfclass", "little_endian"), _VARIANTS, ids=_VARIANT_IDS)
    def test_every_binding_and_visibility_combination(
        self, elfclass, little_endian
    ) -> None:
        """The full cross product, not the values this host happens to emit.

        Exhaustive over a small domain: every binding pyelftools names by
        every visibility it names. A mask error (`& 0x3` vs `& 0x7`, or a
        shift of 3 vs 4) shows up on some cell of this grid and on no
        single hand-picked symbol.
        """
        binds = [b for b in ENUM_ST_INFO_BIND if b != "_default_"]
        visibilities = [v for v in ENUM_ST_VISIBILITY if v != "_default_"]
        entries = [
            _entry(bind=b, vis=v, name=n % len(_STRTAB_BLOB))
            for n, (b, v) in enumerate(itertools.product(binds, visibilities))
        ]
        elffile, section = _table(elfclass, little_endian, entries)
        got = list(iter_symbol_fields(section, elffile))
        assert got == _oracle(section)
        assert len(got) == len(binds) * len(visibilities) > 1

    @pytest.mark.parametrize(("elfclass", "little_endian"), _VARIANTS, ids=_VARIANT_IDS)
    def test_reserved_section_indices_round_trip(self, elfclass, little_endian) -> None:
        """`SHN_UNDEF`/`SHN_ABS`/`SHN_COMMON`/`SHN_XINDEX` and ordinary ones.

        These decide whether a symbol is skipped outright, so a wrong
        value here changes the export set directly.
        """
        reserved = [v for k, v in ENUM_ST_SHNDX.items() if k != "_default_"]
        entries = [_entry(shndx=s) for s in [*reserved, 0, 1, 42, 0xFEFF]]
        elffile, section = _table(elfclass, little_endian, entries)
        assert list(iter_symbol_fields(section, elffile)) == _oracle(section)

    @pytest.mark.parametrize(("elfclass", "little_endian"), _VARIANTS, ids=_VARIANT_IDS)
    def test_every_symbol_type_is_transparent(self, elfclass, little_endian) -> None:
        """`st_info`'s low nibble must not bleed into the decoded binding."""
        types = [t for t in ENUM_ST_INFO_TYPE if t != "_default_"]
        entries = [_entry(sym_type=t, bind="STB_WEAK") for t in types]
        elffile, section = _table(elfclass, little_endian, entries)
        got = list(iter_symbol_fields(section, elffile))
        assert got == _oracle(section)
        assert {g[1] for g in got} == {STB_WEAK}, "symbol type leaked into binding"

    @pytest.mark.parametrize(("elfclass", "little_endian"), _VARIANTS, ids=_VARIANT_IDS)
    def test_extreme_value_and_size_do_not_shift_the_other_fields(
        self, elfclass, little_endian
    ) -> None:
        """ELF32 orders value/size *before* info/other; ELF64 after.

        A format string reused across the two classes decodes this case
        wrongly while still looking plausible on a zero-valued symbol,
        which is why the values here are saturated rather than small.
        """
        top = 0xFFFFFFFF if elfclass == 32 else 0xFFFFFFFFFFFFFFFF
        entries = [
            _entry(value=top, size=top, bind="STB_WEAK", vis="STV_PROTECTED", shndx=7),
            _entry(value=0, size=0, bind="STB_LOCAL", vis="STV_HIDDEN", shndx=0),
        ]
        elffile, section = _table(elfclass, little_endian, entries)
        assert list(iter_symbol_fields(section, elffile)) == _oracle(section)

    @pytest.mark.parametrize(("elfclass", "little_endian"), _VARIANTS, ids=_VARIANT_IDS)
    def test_a_nonzero_section_offset_is_honoured(
        self, elfclass, little_endian
    ) -> None:
        """A real table never starts at byte 0 of the file."""
        entries = [_entry(name=1), _entry(name=7, bind="STB_WEAK")]
        elffile, section = _table(elfclass, little_endian, entries, offset=1024)
        assert list(iter_symbol_fields(section, elffile)) == _oracle(section)

    @pytest.mark.parametrize(("elfclass", "little_endian"), _VARIANTS, ids=_VARIANT_IDS)
    def test_an_empty_table_yields_no_symbols_rather_than_none(
        self, elfclass, little_endian
    ) -> None:
        """ "Supported and empty" must not be confused with "unsupported"."""
        elffile, section = _table(elfclass, little_endian, [])
        fields = iter_symbol_fields(section, elffile)
        assert fields is not None, "an empty table was reported as unsupported"
        assert list(fields) == []


class TestRefusesWhatItCannotDecode:
    """Every refusal returns `None`, never a wrong or empty answer."""

    def test_an_unexpected_entry_size_falls_back(self) -> None:
        """A stride this module does not know is not a table it can read.

        The header is mutated after construction because pyelftools
        validates `sh_size % sh_entsize` itself and refuses to build the
        section at all -- which is its own way of declining this input,
        and not the condition under test here.
        """
        entries = [_entry(), _entry(name=7), _entry(name=12)]
        elffile, section = _table(64, True, entries)
        section.header.sh_entsize = 32
        assert iter_symbol_fields(section, elffile) is None

    @pytest.mark.parametrize("elfclass", [0, 16, 48, 128])
    def test_an_unsupported_elf_class_falls_back(self, elfclass) -> None:
        elffile, section = _table(64, True, [_entry()])
        elffile.elfclass = elfclass
        assert iter_symbol_fields(section, elffile) is None

    def test_a_partial_trailing_record_falls_back(self) -> None:
        """A size that is not a whole multiple of the record must not truncate.

        Decoding the whole records and dropping the remainder would be the
        tempting shortcut, and would silently disagree with pyelftools
        about a corrupt table.

        The stream is padded so the read *succeeds*: without that, the
        short-read guard refuses first and this test passes even with the
        multiple-of-record check deleted, which mutation testing showed it
        did. A refusal test has to reach the condition it names.
        """
        elffile, section = _table(64, True, [_entry(), _entry(name=7)], pad=64)
        section.header.sh_size = section.header.sh_size + 3
        assert iter_symbol_fields(section, elffile) is None

    def test_a_partial_record_check_is_not_masked_by_the_short_read_check(
        self,
    ) -> None:
        """Vacuity guard for the test above: the padded read really succeeds.

        If the padding stopped covering the oversized `sh_size`, the test
        above would silently revert to exercising the short-read guard.
        """
        elffile, section = _table(64, True, [_entry(), _entry(name=7)], pad=64)
        section.header.sh_size = section.header.sh_size + 24
        fields = iter_symbol_fields(section, elffile)
        assert fields is not None, "the padded stream did not satisfy the read"
        assert len(list(fields)) == 3

    def test_a_short_read_falls_back(self) -> None:
        """A table claiming more bytes than the file holds keeps pyelftools'
        behaviour rather than silently losing its tail."""
        elffile, section = _table(64, True, [_entry(), _entry(name=7)])
        section.header.sh_size = section.header.sh_size + 24 * 50
        assert iter_symbol_fields(section, elffile) is None

    def test_an_oversized_table_falls_back(self, monkeypatch) -> None:
        """A corrupt `sh_size` must not become an arbitrary allocation.

        The cap is lowered rather than a 256 MB stream built, and the
        bytes really are present, so the refusal must come from the cap
        itself. Sizing past the real cap instead makes the read fail
        first, and the short-read guard then answers -- mutation testing
        showed that version passed with the cap check deleted.
        """
        import abicheck.extract.elf_symbol_fastpath as mod

        entries = [_entry(), _entry(name=7), _entry(name=12)]
        elffile, section = _table(64, True, entries)
        monkeypatch.setattr(mod, "MAX_BULK_TABLE_BYTES", 24)
        assert section.header.sh_size > 24
        assert iter_symbol_fields(section, elffile) is None

    def test_a_table_at_the_cap_is_still_decoded(self, monkeypatch) -> None:
        """Vacuity guard: the cap rejects what exceeds it, not everything."""
        import abicheck.extract.elf_symbol_fastpath as mod

        elffile, section = _table(64, True, [_entry()])
        monkeypatch.setattr(mod, "MAX_BULK_TABLE_BYTES", section.header.sh_size)
        fields = iter_symbol_fields(section, elffile)
        assert fields is not None
        assert len(list(fields)) == 1

    def test_a_malformed_header_falls_back(self) -> None:
        elffile, section = _table(64, True, [_entry()])
        section.header.sh_entsize = "not-a-number"
        assert iter_symbol_fields(section, elffile) is None

    def test_a_stream_that_raises_falls_back(self) -> None:
        class Exploding:
            def seek(self, *_a, **_k):
                raise OSError("device on fire")

            def read(self, *_a, **_k):  # pragma: no cover - seek raises first
                raise OSError("device on fire")

        elffile, section = _table(64, True, [_entry()])
        elffile.stream = Exploding()
        section.stream = Exploding()
        assert iter_symbol_fields(section, elffile) is None


class TestConstantsTrackPyelftools:
    """The integers this module filters on are pyelftools', not folklore."""

    @pytest.mark.parametrize(
        ("name", "value", "enum"),
        [
            ("STB_GLOBAL", STB_GLOBAL, ENUM_ST_INFO_BIND),
            ("STB_WEAK", STB_WEAK, ENUM_ST_INFO_BIND),
            ("STV_INTERNAL", STV_INTERNAL, ENUM_ST_VISIBILITY),
            ("STV_HIDDEN", STV_HIDDEN, ENUM_ST_VISIBILITY),
            ("SHN_UNDEF", SHN_UNDEF, ENUM_ST_SHNDX),
            ("SHN_ABS", SHN_ABS, ENUM_ST_SHNDX),
        ],
    )
    def test_each_constant_matches(self, name, value, enum) -> None:
        assert enum[name] == value

    def test_the_import_time_check_fires_on_a_renumbering(self, monkeypatch) -> None:
        """The guard must actually fail, not merely exist.

        Without this the agreement check could be reduced to a no-op and
        every assertion above would still pass, since they read the same
        enums it does.
        """
        import abicheck.extract.elf_symbol_fastpath as mod

        monkeypatch.setitem(ENUM_ST_INFO_BIND, "STB_GLOBAL", 9)
        with pytest.raises(RuntimeError, match="stale"):
            mod._assert_enums_agree()
