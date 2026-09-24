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

"""The ELF symbol table is decoded once, and ``.gnu.version`` in one call.

Both are pure speedups over pyelftools' per-entry reads, so the oracle is
those per-entry reads themselves (``iter_symbols``, ``get_symbol``), run on
real shared objects: the host's own versioned libraries, plus one this test
links with a version script so the hidden-version bit is present.
"""

from __future__ import annotations

import glob
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from elftools.elf.elffile import ELFFile
from elftools.elf.gnuversions import GNUVerSymSection
from elftools.elf.sections import SymbolTableSection

from abicheck.elf_metadata import _parse_ver_entries
from abicheck.extract.elf_symbol_versions import decode_versym, symbols_of

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="needs ELF shared objects and an ELF linker"
)

_HOST_LIBS = sorted(
    p
    for p in glob.glob("/usr/lib/x86_64-linux-gnu/lib*.so.*")
    + glob.glob("/lib/x86_64-linux-gnu/libc.so.*")
    if Path(p).is_file()
)[:25]


@pytest.fixture(scope="module")
def hidden_version_lib(tmp_path_factory: pytest.TempPathFactory) -> Path | None:
    cc = shutil.which("gcc") or shutil.which("cc")
    if cc is None:
        return None
    d = tmp_path_factory.mktemp("ver")
    (d / "v.c").write_text(
        "int f_old(void){return 1;}\nint f_new(void){return 2;}\n"
        '__asm__(".symver f_old,f@V1");\n__asm__(".symver f_new,f@@V2");\n'
    )
    (d / "v.map").write_text("V1 { local: *; };\nV2 { global: f; } V1;\n")
    out = d / "libv.so"
    proc = subprocess.run(
        [
            cc,
            "-shared",
            "-fPIC",
            "-o",
            str(out),
            str(d / "v.c"),
            f"-Wl,--version-script={d / 'v.map'}",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return out


def _libs(hidden: Path | None) -> list[Path]:
    libs = [Path(p) for p in _HOST_LIBS]
    if hidden is not None:
        libs.append(hidden)
    if not libs:
        pytest.skip("no ELF shared objects on this host")
    return libs


def test_decode_versym_equals_the_per_entry_read(
    hidden_version_lib: Path | None,
) -> None:
    checked = hidden_bits = 0
    for lib in _libs(hidden_version_lib):
        with open(lib, "rb") as fh:
            for sec in ELFFile(fh).iter_sections():
                if not isinstance(sec, GNUVerSymSection):
                    continue
                n = sec.num_symbols()
                fast = decode_versym(sec, n)
                assert fast == _parse_ver_entries(sec, n, lib), lib
                checked += 1
                hidden_bits += sum(hidden for _, hidden in fast or [])
    assert checked, "no .gnu.version section was exercised"
    if hidden_version_lib is not None:
        assert hidden_bits, "the hidden-version bit was never exercised"


def test_decode_versym_declines_a_short_section() -> None:
    class _Sec:
        header = {"sh_entsize": 2, "sh_size": 4}

    assert decode_versym(_Sec(), 3) is None


def test_symbols_of_matches_iter_symbols_and_walks_once() -> None:
    for lib in _libs(None)[:10]:
        with open(lib, "rb") as fh:
            for sec in ELFFile(fh).iter_sections():
                if not isinstance(sec, SymbolTableSection):
                    continue
                expected = [(s.name, s.entry) for s in sec.iter_symbols()]
                first = list(symbols_of(sec))
                second = list(symbols_of(sec))
                assert [(s.name, s.entry) for s in first] == expected
                assert all(a is b for a, b in zip(first, second))


def test_a_walk_that_fails_is_not_cached() -> None:
    class _Sec:
        def __init__(self) -> None:
            self.calls = 0

        def iter_symbols(self):  # noqa: ANN202
            self.calls += 1
            yield "a"
            raise ValueError("corrupt entry")

    sec = _Sec()
    for _ in range(2):
        with pytest.raises(ValueError):
            list(symbols_of(sec))
    assert sec.calls == 2


def test_short_section_data_is_declined() -> None:
    class _Sec:
        header = {"sh_entsize": 2, "sh_size": 8}

        def data(self) -> bytes:
            return b"\x00\x00"  # header claims 4 entries, bytes hold 1

    assert decode_versym(_Sec(), 4) is None


def test_parse_falls_back_to_the_per_entry_read_with_identical_output(
    monkeypatch: pytest.MonkeyPatch, hidden_version_lib: Path | None
) -> None:
    import abicheck.elf_metadata as elf_metadata

    lib = hidden_version_lib or _libs(None)[0]
    fast = repr(elf_metadata.parse_elf_metadata(lib))
    monkeypatch.setattr(elf_metadata, "decode_versym", lambda _s, _n: None)
    assert repr(elf_metadata.parse_elf_metadata(lib)) == fast


def test_an_unreadable_section_is_declined_not_raised() -> None:
    class _Sec:
        header = {"sh_entsize": 2, "sh_size": 8}

        def data(self) -> bytes:
            raise ValueError("truncated file")

    assert decode_versym(_Sec(), 4) is None
