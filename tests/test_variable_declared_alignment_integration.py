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

"""A header's alignment specifier on an exported object, every old/new pair.

The header declares ``extern int g_data`` with no specifier, ``aligned(16)``
or ``aligned(64)``; the unit defining it includes the header. For each pair
of those, under gcc and clang, the library is dumped binary-only, with DWARF,
and with headers through castxml and the clang backend.

Oracle, from the source and ``nm`` alone: the declared alignment is the
specifier or ``int``'s 4 bytes. Where the evidence states declarations
(DWARF, the clang header backend) a declared alignment the recorded facts
prove changed is reported as ``var_alignment_changed`` and an unchanged one
never is; a gcc split declaration without a specifier is recorded as a lower
bound, which an exact value at or above it does not contradict. At every level, a
reduction the new placement shows against the old declared requirement is
reported (never vetoed by a value the evidence could not state), and an
unchanged declaration reports no alignment finding at all.
"""

from __future__ import annotations

import functools
import itertools
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.checker import compare
from abicheck.dumper import dump

# The oracle reads placement with `nm -D` from an ELF .so built with DWARF.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform != "linux", reason="builds ELF .so files with DWARF"
    ),
]

_ALIGNS = (None, 16, 64)
_EVIDENCE = ("binary", "dwarf", "castxml", "clang")
_STATES_DECLARATIONS = frozenset({"dwarf", "clang"})
_ALIGNMENT_KINDS = frozenset(
    {"var_alignment_changed", "exported_object_alignment_reduced"}
)


def _declared(align: int | None) -> int:
    return align or 4


def _bound_only(cc: str, evidence: str, align: int | None) -> bool:
    """Whether the evidence records only a lower bound for this side.

    gcc writes ``DW_AT_alignment`` on neither entry of an ``extern``
    declaration / definition split, so a side without a header specifier
    cannot be told from one whose definition-only specifier was lost: its
    declared alignment is at least the type's, nothing more.
    """
    return cc == "gcc" and evidence == "dwarf" and align is None


def _provably_differs(
    cc: str,
    evidence: str,
    old: int | None,
    new: int | None,
    placed: tuple[int, int],
) -> bool:
    """Whether the recorded facts prove the declared alignments differ.

    A bound-only side's alignment is at least its type's and at most its
    address alignment (an object aligned to A sits at a multiple of A; read
    with ``nm``, not from abicheck); a page-sized placement bounds nothing.
    """
    lo_o, lo_n = _declared(old), _declared(new)

    def high(bound: bool, lo: int, placement: int) -> float:
        if not bound:
            return lo
        return placement if lo <= placement < 4096 else float("inf")

    hi_o = high(_bound_only(cc, evidence, old), lo_o, placed[0])
    hi_n = high(_bound_only(cc, evidence, new), lo_n, placed[1])
    return hi_o < lo_n or hi_n < lo_o


@functools.cache
def _build(work: Path, cc: str, align: int | None, debug: bool) -> Path:
    d = work / f"{cc}-{align}-{'g' if debug else 'nog'}"
    d.mkdir(parents=True)
    spec = f" __attribute__((aligned({align})))" if align else ""
    (d / "lib.h").write_text(f"extern int g_data{spec};\nint get(void);\n")
    (d / "lib.c").write_text(
        '#include "lib.h"\nint pad = 2;\nint g_data = 1;\n'
        "int get(void) { return g_data + pad; }\n"
    )
    so = d / "libal.so"
    cmd = [cc, "-O0", *(["-g"] if debug else []), "-shared", "-fPIC"]
    cmd += ["-o", str(so), str(d / "lib.c")]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"{cmd}\n{proc.stderr}"
    return so


@functools.cache
def _snapshot(work: Path, cc: str, align: int | None, evidence: str):
    so = _build(work, cc, align, evidence != "binary")
    headers = [so.parent / "lib.h"] if evidence in ("castxml", "clang") else []
    backend = evidence if headers else "clang"
    return dump(so, headers, compiler="cc", lang="c", header_backend=backend)


def _placement(so: Path, name: str = "g_data") -> int:
    """The largest power of two dividing *name*'s address, from ``nm``."""
    out = subprocess.run(
        ["nm", "-D", "--defined-only", str(so)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    value = next(
        int(line.split()[0], 16)
        for line in out.splitlines()
        if line.endswith(f" {name}")
    )
    return value & -value if value else 1 << 12


@pytest.fixture(scope="module")
def work(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("declared-alignment")


@pytest.mark.parametrize(("old", "new"), list(itertools.product(_ALIGNS, repeat=2)))
@pytest.mark.parametrize("evidence", _EVIDENCE)
@pytest.mark.parametrize("cc", ["gcc", "clang"])
def test_a_header_alignment_change_reads_as_the_declaration_says(
    work, cc, evidence, old, new
) -> None:
    if shutil.which(cc) is None or shutil.which("nm") is None:
        pytest.skip(f"{cc} or nm not installed")
    if evidence == "castxml" and shutil.which("castxml") is None:
        pytest.skip("castxml not installed")
    snaps = [_snapshot(work, cc, a, evidence) for a in (old, new)]
    kinds = {c.kind.value for c in compare(*snaps).changes if c.symbol == "g_data"}
    declared_old, declared_new = _declared(old), _declared(new)

    if declared_old == declared_new:
        assert not kinds & _ALIGNMENT_KINDS, kinds
        return
    placed_new = _placement(_build(work, cc, new, evidence != "binary"))
    placed_old = _placement(_build(work, cc, old, evidence != "binary"))
    if evidence in _STATES_DECLARATIONS:
        # Reported exactly when the recorded facts prove it: gcc's bound-only
        # side is closed above by its address, so a header specifier dropped
        # or added is reported whenever the placement rules the other side's
        # value out (known gap when it does not, canary
        # test_gcc_dwarf_loses_a_definition_only_alignas).
        provable = _provably_differs(cc, evidence, old, new, (placed_old, placed_new))
        assert ("var_alignment_changed" in kinds) is provable, (
            kinds,
            placed_old,
            placed_new,
        )
    if placed_new < declared_old <= placed_old:
        assert "exported_object_alignment_reduced" in kinds, kinds


@pytest.mark.parametrize("lane", ["dwarf", "clang"])
@pytest.mark.parametrize("cc", ["gcc", "clang"])
def test_a_retype_that_changes_alignment_reads_alike_in_every_lane(
    tmp_path: Path, cc: str, lane: str
) -> None:
    """``int g`` -> ``double g`` (x86-64: alignof 4 -> 8) is BREAKING through
    ``var_type_changed``; ``var_alignment_changed`` rides along wherever the
    evidence states both declarations -- the clang header lane did so before
    generation 51, and DWARF now agrees with it -- or the old object's address
    rules the new alignment out (gcc's declaration/definition split records
    bounds). Never a different verdict."""
    if shutil.which(cc) is None:
        pytest.skip(f"{cc} not installed")
    snaps, placed = [], []
    for ty in ("int", "double"):
        d = tmp_path / f"{cc}-{ty}"
        d.mkdir()
        (d / "lib.h").write_text(f"extern {ty} g;\n")
        (d / "lib.c").write_text(f'#include "lib.h"\n{ty} g = 1;\n')
        so = d / "lib.so"
        cmd = [cc, "-g", "-O0", "-shared", "-fPIC", "-o", str(so), str(d / "lib.c")]
        subprocess.run(cmd, check=True, capture_output=True)
        placed.append(_placement(so, "g"))
        headers = [d / "lib.h"] if lane == "clang" else []
        snaps.append(dump(so, headers, compiler="cc", lang="c", header_backend="clang"))
    result = compare(*snaps)
    kinds = {c.kind.value for c in result.changes if c.symbol == "g"}
    assert "var_type_changed" in kinds, kinds
    assert result.verdict.value == "BREAKING"
    # gcc DWARF records both sides as bounds (int >= 4, double >= 8 bytes);
    # they are disjoint only when the old object's address rules out 8.
    states_both = lane == "clang" or cc == "clang"
    provable = states_both or placed[0] < 8
    assert ("var_alignment_changed" in kinds) is provable, (kinds, placed)
