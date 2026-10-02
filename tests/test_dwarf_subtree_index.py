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

"""tests/test_dwarf_subtree_index.py -- skipping DWARF subtrees without parsing them.

The oracle throughout is pyelftools' own, unpatched
``CompileUnit.iter_DIE_children`` -- which shares no code with the index --
run over real compiled binaries from every available producer (GCC emits
``DW_AT_sibling``, clang never does), DWARF version, optimization level and
DWARF64. The invariants:

* the indexed iterator yields exactly the stock children, in order, for
  **every** DIE of every unit (not only the ones a walker happens to visit);
* ``subtree_ends`` agrees with the offset the stock iterator reaches past
  each subtree's null terminator;
* ``iter_children_tagged`` equals filtering the stock children by tag;
* a unit the scanner cannot size falls back to the stock path rather than
  guessing;
* and the whole DWARF parse produces identical metadata either way.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from elftools.dwarf.compileunit import CompileUnit
from elftools.elf.elffile import ELFFile

from abicheck.extract import dwarf_subtree_index as dsi

_SRC = """
    #include <map>
    #include <string>
    #include <vector>
    #include <functional>
    #include <sstream>
    namespace api {
    struct Point { int x; int y; double w; char tag[3]; };
    struct __attribute__((packed)) Packed { char c; int i; };
    class Base { public: virtual ~Base(); virtual int f(int) const; int v = 0; };
    Base::~Base() {}
    int Base::f(int a) const { return a + v; }
    template <typename T> struct Box { T value; T get() const { return value; } };
    std::string render(const std::vector<int>& v, std::function<int(int)> g) {
        std::ostringstream o;
        for (int x : v) { if (x > 2) { int y = g(x); o << y; } }
        return o.str();
    }
    int use(Box<long> b, Point p, Packed k) {
        std::map<int, std::string> m{{1, "a"}};
        return static_cast<int>(b.get()) + p.x + k.i + static_cast<int>(m.size());
    }
    const char* utf8() { return "\\u00e9\\u00e8"; }
    }
"""

#: (compiler, extra flags) -- each a distinct DWARF shape the index must size.
_BUILDS = [
    ("clang++", ["-O2", "-gdwarf-5"]),
    ("clang++", ["-O2", "-gdwarf-4"]),
    ("clang++", ["-O0", "-gdwarf-5"]),
    ("clang++", ["-O2", "-gdwarf-5", "-gdwarf64"]),
    ("g++", ["-O2", "-gdwarf-5"]),
    ("g++", ["-O2", "-gdwarf-4"]),
    ("g++", ["-O0", "-gdwarf-2"]),
]

_TAG_SETS = [
    frozenset({"DW_TAG_formal_parameter"}),
    frozenset({"DW_TAG_member", "DW_TAG_subprogram"}),
    frozenset({"DW_TAG_no_such_tag"}),
]

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="compiles ELF shared objects"
)


def _build(tmp_path: Path, compiler: str, flags: list[str]) -> Path:
    if shutil.which(compiler) is None:
        pytest.skip(f"{compiler} not found in PATH")
    src = tmp_path / "t.cpp"
    src.write_text(textwrap.dedent(_SRC), encoding="utf-8")
    out = tmp_path / "libt.so"
    r = subprocess.run(  # noqa: S603 - fixed argv, never shell=True
        [compiler, "-shared", "-fPIC", *flags, "-o", str(out), str(src)],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        detail = (
            f"{compiler} {' '.join(flags)} exited {r.returncode}: {r.stderr[:2000]}"
        )
        if "-gdwarf64" in flags:
            # A named optional feature: older toolchains lack DWARF64.
            pytest.skip(detail)
        # A present compiler rejecting this fixture is a broken fixture or
        # toolchain, never an absent capability (tests/CLAUDE.md).
        pytest.fail(detail)
    return out


def _stock_children(CU: Any, die: Any) -> list[Any]:
    return list(CompileUnit.iter_DIE_children(CU, die))


def _all_dies(CU: Any) -> list[Any]:
    """Every DIE of *CU*, reached through the *stock* iterator only."""
    out: list[Any] = []
    stack = [CU.get_top_DIE()]
    while stack:
        die = stack.pop()
        out.append(die)
        stack.extend(_stock_children(CU, die))
    return out


@pytest.fixture(params=_BUILDS, ids=lambda b: f"{b[0]}{''.join(b[1])}")
def binary(request: pytest.FixtureRequest, tmp_path: Path) -> Path:
    compiler, flags = request.param
    return _build(tmp_path, compiler, flags)


class TestAgainstStockPyelftools:
    def test_children_of_every_die_match(self, binary: Path) -> None:
        with binary.open("rb") as fh:
            dwarf = dsi.open_indexed_dwarf_info(ELFFile(fh))
            compared = 0
            for CU in dwarf.iter_CUs():
                for die in _all_dies(CU):
                    stock = [c.offset for c in _stock_children(CU, die)]
                    indexed = [c.offset for c in die.iter_children()]
                    assert indexed == stock, f"DIE 0x{die.offset:x}"
                    compared += 1
            assert compared > 1000  # vacuity: a real tree was compared

    def test_subtree_ends_match_the_stock_terminators(self, binary: Path) -> None:
        with binary.open("rb") as fh:
            dwarf = ELFFile(fh).get_dwarf_info()  # un-indexed: the oracle
            for CU in dwarf.iter_CUs():
                ends = dsi.subtree_ends(CU)
                assert ends is not None, "a real unit must be sizable"
                parents = [d for d in _all_dies(CU) if d.has_children]
                expected = {
                    d.offset: d._terminator.offset + d._terminator.size for d in parents
                }
                assert ends == expected

    @pytest.mark.parametrize("tags", _TAG_SETS, ids=lambda t: "+".join(sorted(t)))
    def test_tagged_children_equal_a_filter_of_the_stock_ones(
        self, binary: Path, tags: frozenset[str]
    ) -> None:
        with binary.open("rb") as fh:
            dwarf = dsi.open_indexed_dwarf_info(ELFFile(fh))
            for CU in dwarf.iter_CUs():
                for die in _all_dies(CU):
                    expected = [
                        c.offset for c in _stock_children(CU, die) if c.tag in tags
                    ]
                    got = [c.offset for c in dsi.iter_children_tagged(die, tags)]
                    assert got == expected, f"DIE 0x{die.offset:x}"


def test_clang_output_actually_takes_the_indexed_path(tmp_path: Path) -> None:
    """Vacuity guard: the fixture must contain the case the index exists for.

    Without a parent that lacks ``DW_AT_sibling``, every comparison above
    would pass through the stock ``DW_AT_sibling`` branch and prove nothing.
    """
    binary = _build(tmp_path, "clang++", ["-O2", "-gdwarf-5"])
    with binary.open("rb") as fh:
        dwarf = dsi.open_indexed_dwarf_info(ELFFile(fh))
        unlinked = 0
        for CU in dwarf.iter_CUs():
            for die in _all_dies(CU):
                if die.has_children and "DW_AT_sibling" not in die.attributes:
                    unlinked += 1
            list(CU.get_top_DIE().iter_children())
            assert CU.__dict__.get("_abicheck_die_index") is not None
        assert unlinked > 50


def test_an_unsizable_unit_falls_back_to_the_stock_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A form the scanner cannot size must decline, never guess."""
    binary = _build(tmp_path, "clang++", ["-O2", "-gdwarf-5"])
    fixed = dict(dsi._FIXED_FORMS)
    del fixed["DW_FORM_data1"]  # used by every clang unit (decl_file/decl_line)
    monkeypatch.setattr(dsi, "_FIXED_FORMS", fixed)
    with binary.open("rb") as fh:
        dwarf = dsi.open_indexed_dwarf_info(ELFFile(fh))
        for CU in dwarf.iter_CUs():
            assert dsi.subtree_ends(CU) is None
            for die in _all_dies(CU):
                assert [c.offset for c in die.iter_children()] == [
                    c.offset for c in _stock_children(CU, die)
                ]
                tags = frozenset({"DW_TAG_formal_parameter"})
                assert [c.offset for c in dsi.iter_children_tagged(die, tags)] == [
                    c.offset for c in _stock_children(CU, die) if c.tag in tags
                ]


def test_drop_index_forces_a_rebuild_with_the_same_answer(tmp_path: Path) -> None:
    binary = _build(tmp_path, "clang++", ["-O2", "-gdwarf-5"])
    with binary.open("rb") as fh:
        dwarf = dsi.open_indexed_dwarf_info(ELFFile(fh))
        CU = next(iter(dwarf.iter_CUs()))
        first = dsi.subtree_ends(CU)
        dsi.drop_index(CU)
        assert "_abicheck_die_index" not in CU.__dict__
        assert dsi.subtree_ends(CU) == first


@pytest.mark.parametrize("compiler", ["clang++", "g++"])
def test_parse_dwarf_output_is_unchanged_by_the_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, compiler: str
) -> None:
    """End to end: the metadata a dump reads is identical with the index off."""
    from abicheck import dwarf_unified
    from abicheck.elf_metadata import parse_elf_metadata
    from abicheck.workflows.dwarf_snapshot_assembly import build_snapshot_from_dwarf

    binary = _build(tmp_path, compiler, ["-O2", "-gdwarf-5"])
    elf_meta = parse_elf_metadata(binary)
    indexed = dwarf_unified.parse_dwarf(binary)
    indexed_snap = build_snapshot_from_dwarf(binary, elf_meta, *indexed)

    import abicheck.dwarf_snapshot as dsnap

    for mod in (dwarf_unified, dsnap):
        monkeypatch.setattr(
            mod, "open_indexed_dwarf_info", lambda elf: elf.get_dwarf_info()
        )
    monkeypatch.setattr(
        dsi,
        "iter_formal_parameters",
        lambda die: (
            c for c in die.iter_children() if c.tag == "DW_TAG_formal_parameter"
        ),
    )
    stock = dwarf_unified.parse_dwarf(binary)
    stock_snap = build_snapshot_from_dwarf(binary, elf_meta, *stock)

    assert indexed[0].structs  # vacuity: something was actually extracted
    assert indexed == stock
    assert indexed_snap == stock_snap
