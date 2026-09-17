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

"""``buffered_string_table`` must be indistinguishable from pyelftools.

This module shadows a third-party accessor to make it faster, so the only
interesting property is *equivalence*: for every offset, buffered and
unbuffered must return the same string. The oracle is therefore
pyelftools' own ``StringTableSection.get_string`` -- never a
reimplementation of the NUL scan in the test, which would just be the
implementation under test written twice.

Equivalence is asserted over an **exhaustive** sweep of every byte offset
in a synthetic table, not over the offsets a well-formed symbol table
happens to use. That is deliberate: the interesting offsets are precisely
the malformed ones (past the end, mid-string, pointing at a NUL, negative),
and pyelftools has real, easy-to-get-wrong behaviour at each -- notably
that it does **not** bound its read by ``sh_size`` and instead reads on
into the following bytes, and that a table with no terminating NUL before
EOF yields ``''``.

The equivalence is bidirectional evidence, so a vacuity guard matters: a
sweep where buffering never engaged would pass trivially. Every test here
asserts the engaged/fell-through state explicitly rather than inferring it.
"""

from __future__ import annotations

import io

import pytest

from abicheck.extract.elf_string_table import (
    MAX_BUFFERED_TABLE_BYTES,
    buffered_string_table,
    string_table_of,
)


class _FakeStringTable:
    """The minimum surface the buffer uses, with pyelftools' own semantics.

    ``get_string`` is copied from ``StringTableSection.get_string``
    verbatim (a stream seek plus ``parse_cstring_from_stream``), so it is
    the real oracle rather than a paraphrase of it: an unterminated table
    returns ``''`` and an out-of-range offset reads into whatever follows.
    """

    def __init__(self, table: bytes, *, prefix: bytes = b"", suffix: bytes = b""):
        self._header = {"sh_offset": len(prefix), "sh_size": len(table)}
        self.stream = io.BytesIO(prefix + table + suffix)

    def __getitem__(self, key: str) -> int:
        return self._header[key]

    def get_string(self, offset: int) -> str:
        from elftools.common.utils import parse_cstring_from_stream

        s = parse_cstring_from_stream(self.stream, self._header["sh_offset"] + offset)
        return s.decode("utf-8", errors="replace") if s else ""


_TABLE = b"\x00_ZN3ns3fooEv\x00_ZN3ns3barEi\x00\x00trailing\x00\xff\xfe\x00last\x00"


def _outcome(table: _FakeStringTable, offset: int) -> object:
    """The full observable result of one lookup: a value *or* a raise.

    Equivalence has to cover both, because pyelftools genuinely raises for
    some offsets a malformed table can contain -- a negative ``st_name``
    resolving to a negative stream position raises ``ValueError`` from the
    seek. A buffered rewrite that quietly returned ``''`` there would be
    *more* forgiving than the code it replaces, which is still a behaviour
    change, and one that would hide a corrupt table instead of surfacing
    it.
    """
    try:
        return ("value", table.get_string(offset))
    except Exception as exc:  # noqa: BLE001 - the exception *is* the outcome
        return ("raise", type(exc).__name__)


class TestEquivalenceAgainstPyelftools:
    @pytest.mark.parametrize(
        ("prefix", "suffix"),
        [
            (b"", b""),
            (b"ELFHEADERPADDING", b""),
            (b"", b"following-section-bytes\x00"),
            (b"pad", b"more\x00data"),
        ],
    )
    def test_every_offset_agrees(self, prefix: bytes, suffix: bytes) -> None:
        """Exhaustive over the table, and past its end into the next section."""
        plain = _FakeStringTable(_TABLE, prefix=prefix, suffix=suffix)
        probe = _FakeStringTable(_TABLE, prefix=prefix, suffix=suffix)
        # Range deliberately overruns sh_size: the out-of-bounds read is the
        # behaviour most likely to be "tidied up" by a buffered rewrite.
        offsets = range(-4, len(_TABLE) + 12)
        expected = [_outcome(plain, o) for o in offsets]
        with buffered_string_table(probe) as engaged:
            assert engaged
            got = [_outcome(probe, o) for o in offsets]
        assert got == expected
        # Vacuity guards on the fixture: the comparison is only meaningful
        # if the sweep actually produced each interesting shape.
        values = [v for kind, v in expected if kind == "value"]
        assert any(values), "the fixture resolved no names at all"
        assert any(v == "" for v in values), "no empty-result offset covered"

    def test_a_table_with_no_terminating_nul_agrees(self) -> None:
        """``parse_cstring_from_stream`` returns ``None`` here, so ``''``.

        The buffered path must not instead return the unterminated tail.
        """
        table = b"\x00unterminated"
        plain = _FakeStringTable(table)
        probe = _FakeStringTable(table)
        offsets = range(0, len(table) + 4)
        expected = [_outcome(plain, o) for o in offsets]
        with buffered_string_table(probe) as engaged:
            assert engaged
            assert [_outcome(probe, o) for o in offsets] == expected
        assert expected[1] == ("value", ""), "oracle missed the unterminated case"

    def test_invalid_utf8_is_replaced_identically(self) -> None:
        table = b"\x00ok\x00\xff\xfe\xfd\x00"
        plain = _FakeStringTable(table)
        probe = _FakeStringTable(table)
        with buffered_string_table(probe) as engaged:
            assert engaged
            for off in range(len(table)):
                assert probe.get_string(off) == plain.get_string(off), off
        assert "�" in plain.get_string(4), "oracle produced no replacement char"

    def test_a_non_integer_offset_is_handed_to_the_original(self) -> None:
        plain = _FakeStringTable(_TABLE)
        probe = _FakeStringTable(_TABLE)
        with buffered_string_table(probe) as engaged:
            assert engaged
            assert _outcome(probe, "1") == _outcome(plain, "1")  # type: ignore[arg-type]

    def test_a_negative_offset_raises_exactly_as_pyelftools_does(self) -> None:
        """The specific outcome the exhaustive sweep first surfaced.

        Named separately so the behaviour is legible without decoding a
        parametrized sweep: a negative offset seeks before the file and
        must keep raising rather than being absorbed into ``''``.
        """
        plain = _FakeStringTable(_TABLE)
        probe = _FakeStringTable(_TABLE)
        assert _outcome(plain, -1) == ("raise", "ValueError")
        with buffered_string_table(probe) as engaged:
            assert engaged
            assert _outcome(probe, -1) == ("raise", "ValueError")


class TestFallThroughAndRestoration:
    def test_an_oversized_table_is_not_buffered(self) -> None:
        probe = _FakeStringTable(b"\x00x\x00")
        probe._header["sh_size"] = MAX_BUFFERED_TABLE_BYTES + 1
        with buffered_string_table(probe) as engaged:
            assert engaged is False

    @pytest.mark.parametrize("size", [0, -1])
    def test_an_empty_or_negative_table_is_not_buffered(self, size: int) -> None:
        probe = _FakeStringTable(b"\x00x\x00")
        probe._header["sh_size"] = size
        with buffered_string_table(probe) as engaged:
            assert engaged is False

    def test_none_is_accepted_and_reports_no_buffering(self) -> None:
        with buffered_string_table(None) as engaged:
            assert engaged is False

    def test_a_section_without_a_stream_falls_through(self) -> None:
        """Nothing to read from, so nothing to buffer.

        A pyelftools section always has ``.stream``, but this module
        deliberately duck-types its input (``string_table_of`` returns
        whatever ``.stringtable`` is), so the absence has to be handled
        rather than raise inside a parse.
        """

        class NoStream(_FakeStringTable):
            def __init__(self) -> None:
                super().__init__(b"\x00x\x00")
                self.stream = None  # type: ignore[assignment]

        with buffered_string_table(NoStream()) as engaged:
            assert engaged is False

    def test_an_unreadable_stream_falls_through(self) -> None:
        """A stream that raises on seek/read must not break the parse.

        The point of the fall-through is that the original accessor still
        gets its chance -- whatever it does with the same broken stream is
        then pyelftools' own behaviour, not a new failure mode introduced
        by buffering.
        """

        class Exploding(_FakeStringTable):
            def __init__(self) -> None:
                super().__init__(b"\x00x\x00")

                class _Boom:
                    def seek(self, *_a: object) -> None:
                        raise OSError("device on fire")

                    def read(self, *_a: object) -> bytes:
                        raise OSError("device on fire")

                self.stream = _Boom()  # type: ignore[assignment]

        with buffered_string_table(Exploding()) as engaged:
            assert engaged is False

    def test_a_malformed_header_falls_through(self) -> None:
        class Broken(_FakeStringTable):
            def __getitem__(self, key: str) -> int:
                raise KeyError(key)

        with buffered_string_table(Broken(b"\x00x\x00")) as engaged:
            assert engaged is False

    def test_a_truncated_read_falls_through(self) -> None:
        """A short read would silently shorten names, so it must not be used."""
        probe = _FakeStringTable(b"\x00abc\x00")
        probe._header["sh_size"] = 999
        probe._header["sh_size"] = 5
        probe.stream = io.BytesIO(b"\x00ab")  # fewer bytes than sh_size
        with buffered_string_table(probe) as engaged:
            assert engaged is False

    def test_the_original_accessor_is_restored_afterwards(self) -> None:
        probe = _FakeStringTable(_TABLE)
        before = probe.get_string(1)
        with buffered_string_table(probe) as engaged:
            assert engaged
        assert "get_string" not in vars(probe)
        assert probe.get_string(1) == before

    def test_it_is_restored_even_when_the_block_raises(self) -> None:
        probe = _FakeStringTable(_TABLE)
        with pytest.raises(RuntimeError), buffered_string_table(probe):
            raise RuntimeError("boom")
        assert "get_string" not in vars(probe)
        assert probe.get_string(1) == "_ZN3ns3fooEv"

    def test_only_the_instance_is_shadowed_not_the_class(self) -> None:
        """A second section object must be unaffected while the first is buffered."""
        first = _FakeStringTable(_TABLE)
        second = _FakeStringTable(_TABLE)
        with buffered_string_table(first):
            assert "get_string" in vars(first)
            assert "get_string" not in vars(second)
            assert second.get_string(1) == "_ZN3ns3fooEv"

    def test_string_table_of_tolerates_a_section_without_one(self) -> None:
        assert string_table_of(object()) is None
        assert string_table_of(None) is None


class TestRealElfParse:
    """Equivalence through the real parser on a real ELF file.

    A unit-level equivalence proof over a synthetic table says nothing
    about whether the buffer is wired into the parse at all, which is the
    "prove both configurations actually ran" rule: this drives
    ``parse_elf_metadata`` twice over a genuine ELF binary, once with the
    cap set to 0 so the guard falls through to pyelftools, and compares the
    full symbol/version output.
    """

    #: The four bytes that make a file ELF. Checked explicitly because a
    #: host compiler produces its *own* platform's format: ``cc -shared``
    #: on macOS emits a Mach-O dylib and on Windows a PE DLL, both happily
    #: named ``libx.so``. An earlier version of this fixture assumed ELF
    #: and the tests below failed on the macOS and Windows lanes -- one on
    #: `ELFError: Magic number does not match`, the other on its own
    #: vacuity guard, because the ELF parser correctly found nothing in a
    #: Mach-O file. The subject here is ELF string tables, so a non-ELF
    #: artifact is out of scope rather than a failure.
    _ELF_MAGIC = b"\x7fELF"

    @classmethod
    def _minimal_so(cls, tmp_path):
        """A real **ELF** shared object, or ``None`` if one can't be built.

        ``None`` covers both "no compiler" and "this host's compiler does
        not emit ELF", which the caller turns into a skip.
        """
        import shutil
        import subprocess

        cc = shutil.which("gcc") or shutil.which("cc") or shutil.which("clang")
        if cc is None:
            return None
        src = tmp_path / "x.c"
        src.write_text(
            "int alpha(int a){return a;}\n"
            "int beta(int a){return a+1;}\n"
            "double gamma_fn(double d){return d;}\n"
            "int shared_state = 3;\n"
        )
        so = tmp_path / "libx.so"
        cmd = [cc, "-shared", "-fPIC", "-o", str(so), str(src)]
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0:
            # A configured compiler that ran and rejected a four-line C
            # file is a broken fixture or toolchain, never an absent
            # capability -- so it must fail with the details rather than
            # vanish as a skip (bug class
            # `guard.absent_capability_vs_real_failure`).
            from tests.test_cross_platform_integration import (
                _require_compile_success,
            )

            _require_compile_success(
                "cc", cmd, src.read_text(), proc, optional_feature=None
            )
        if not so.exists():
            return None
        with open(so, "rb") as fh:
            if fh.read(4) != cls._ELF_MAGIC:
                # Mach-O or PE: this host does not build ELF, which *is* an
                # absent capability for a test about ELF string tables.
                return None
        return so

    def test_parse_output_is_identical_with_and_without_buffering(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        so = self._minimal_so(tmp_path)
        if so is None:
            pytest.skip("no compiler producing an ELF shared object on this host")
        import abicheck.extract.elf_string_table as mod
        from abicheck.elf_metadata import parse_elf_metadata

        def shape(meta):
            return (
                [(s.name, s.version, s.is_default) for s in meta.symbols],
                [(i.name, i.version, i.version_soname) for i in meta.imports],
            )

        buffered = shape(parse_elf_metadata(str(so)))
        monkeypatch.setattr(mod, "MAX_BUFFERED_TABLE_BYTES", 0)
        unbuffered = shape(parse_elf_metadata(str(so)))
        assert buffered == unbuffered
        # Vacuity guard: the comparison is only meaningful if the parse
        # actually recovered the exports this fixture defines.
        names = {n for n, _, _ in buffered[0]}
        assert {"alpha", "beta", "gamma_fn", "shared_state"} <= names

    def test_the_buffer_really_engages_on_a_real_symbol_table(self, tmp_path) -> None:
        """Observe the mechanism, not its output.

        Without this, every equivalence assertion above could be comparing
        the unbuffered path with itself.
        """
        so = self._minimal_so(tmp_path)
        if so is None:
            pytest.skip("no compiler producing an ELF shared object on this host")
        from elftools.elf.elffile import ELFFile

        with open(so, "rb") as fh:
            elf = ELFFile(fh)
            dynsym = elf.get_section_by_name(".dynsym")
            assert dynsym is not None
            strtab = string_table_of(dynsym)
            assert strtab is not None
            with buffered_string_table(strtab) as engaged:
                assert engaged is True
                names = [s.name for s in dynsym.iter_symbols()]
        assert "alpha" in names
