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
from pathlib import Path

import pytest

from abicheck.checker import compare
from abicheck.dumper import dump

pytestmark = pytest.mark.integration

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


def _provably_differs(cc: str, evidence: str, old: int | None, new: int | None) -> bool:
    """Whether the recorded facts prove the declared alignments differ."""
    lo_o, lo_n = _declared(old), _declared(new)
    hi_o = float("inf") if _bound_only(cc, evidence, old) else lo_o
    hi_n = float("inf") if _bound_only(cc, evidence, new) else lo_n
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


def _placement(so: Path) -> int:
    """The largest power of two dividing ``g_data``'s address, from ``nm``."""
    out = subprocess.run(
        ["nm", "-D", "--defined-only", str(so)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    value = next(
        int(line.split()[0], 16)
        for line in out.splitlines()
        if line.endswith(" g_data")
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
    if evidence in _STATES_DECLARATIONS:
        # Reported exactly when the recorded facts prove it; a lower bound the
        # other side's exact value meets proves nothing (known gap, canary
        # test_gcc_dwarf_loses_a_definition_only_alignas).
        provable = _provably_differs(cc, evidence, old, new)
        assert ("var_alignment_changed" in kinds) is provable, kinds
    placed_new = _placement(_build(work, cc, new, evidence != "binary"))
    placed_old = _placement(_build(work, cc, old, evidence != "binary"))
    if placed_new < declared_old <= placed_old:
        assert "exported_object_alignment_reduced" in kinds, kinds
