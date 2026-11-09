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

"""Rebuilding an unchanged ``std::string``-using library reads compatible;
removing one of its own exports in the same rebuild still breaks.

The edit corpus's ``rebuild_optimized`` cell on the ``cxx_stdlib`` seed
(PR #1958): ``-O0`` -> ``-O2`` with no source change read BREAKING or
COMPATIBLE_WITH_RISK on gcc and clang at every evidence level, from weak
``std::``/``__gnu_cxx::`` instantiations ``-O2`` inlines, runtime imports
the old version requirements already covered, and a clang ``std::string``
DIE that is declaration-only at ``-O0``. Every consumer built against the
``-O0`` library runs against the ``-O2`` one (the corpus's execution oracle).

What stays: gcc drops ``__stack_chk_fail`` because ``-O2`` leaves no frame
that needs a canary; the binary cannot show the protector is still on, so
``stack_canary_removed`` keeps its RISK (``docs/contribute/known-gaps.md``).

The ``_GLIBCXX_USE_CXX11_ABI=0`` rebuild under headers is refused as
``not_comparable`` (ADR-050, exit 16): the refusal must name the macro.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.checker import compare
from abicheck.checker_policy import API_BREAK_KINDS, BREAKING_KINDS, RISK_KINDS
from abicheck.cli import main
from abicheck.dumper import dump
from abicheck.model.change_catalog.registry import Verdict
from abicheck.serialization import snapshot_to_json

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="ELF/libstdc++ only"),
]

_HEADER = """\
#ifndef EDIT_SEED_STDLIB_HPP
#define EDIT_SEED_STDLIB_HPP
#include <string>
#pragma GCC visibility push(default)
std::string greet(const std::string& who);
int name_len(const std::string& s);
int add(int a, int b);
#pragma GCC visibility pop
#endif
"""
_GREET = 'std::string greet(const std::string& who) { return "hi " + who; }\n'
_NAME_LEN = "int name_len(const std::string& s) { return (int)s.size(); }\n"
_ADD = "int add(int a, int b) { return a + b; }\n"
_SOURCE = '#include "edit_seed.h"\n' + _GREET + _NAME_LEN + _ADD

_COMPILERS = ("g++", "clang++")
_EVIDENCE = ("binary", "dwarf", "headers")


def _driver(cxx: str) -> str:
    if shutil.which(cxx) is None:
        pytest.skip(f"{cxx} not installed")
    return cxx


def _build(
    tmp: Path, name: str, cxx: str, flags: list[str], source: str, debug: bool
) -> Path:
    d = tmp / name
    d.mkdir()
    (d / "edit_seed.h").write_text(_HEADER)
    (d / "lib.cpp").write_text(source)
    so = d / "libseed.so"
    cmd = [cxx, "-std=c++17", *flags, *(["-g"] if debug else []), "-shared", "-fPIC"]
    cmd += ["-I", str(d), "-o", str(so), str(d / "lib.cpp")]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, (
        f"{cmd}\n{proc.stderr}"
    )  # a real compiler failure fails
    return so


def _snap(so: Path, evidence: str, defines: list[str]):
    headers = [so.parent / "edit_seed.h"] if evidence == "headers" else []
    return dump(
        so,
        headers,
        compiler="c++",
        lang="c++",
        header_backend="castxml",
        gcc_options=" ".join(["-std=c++17", *defines]) if headers else None,
    )


def _not_compatible(result) -> list[tuple[str, str]]:
    """Every finding whose effective verdict is a risk or a break."""
    out = []
    for c in result.changes:
        verdict = c.effective_verdict
        if verdict is None:
            if c.kind in BREAKING_KINDS:
                verdict = Verdict.BREAKING
            elif c.kind in API_BREAK_KINDS:
                verdict = Verdict.API_BREAK
            elif c.kind in RISK_KINDS:
                verdict = Verdict.COMPATIBLE_WITH_RISK
        if verdict in (
            Verdict.BREAKING,
            Verdict.API_BREAK,
            Verdict.COMPATIBLE_WITH_RISK,
        ):
            out.append((c.kind.value, c.symbol))
    return out


@pytest.mark.parametrize("evidence", _EVIDENCE)
@pytest.mark.parametrize("cxx", _COMPILERS)
def test_optimization_rebuild_reads_compatible(tmp_path, cxx, evidence):
    cxx = _driver(cxx)
    debug = evidence != "binary"
    old = _snap(_build(tmp_path, "old", cxx, ["-O0"], _SOURCE, debug), evidence, [])
    new = _snap(_build(tmp_path, "new", cxx, ["-O2"], _SOURCE, debug), evidence, [])
    result = compare(old, new)
    residue = _not_compatible(result)
    # Only the stack-canary observation may remain (see the module docstring).
    assert [k for k, _ in residue if k != "stack_canary_removed"] == [], residue
    expected = (
        {Verdict.COMPATIBLE_WITH_RISK}
        if residue
        else {Verdict.NO_CHANGE, Verdict.COMPATIBLE}
    )
    assert result.verdict in expected


@pytest.mark.parametrize("evidence", _EVIDENCE)
@pytest.mark.parametrize("cxx", _COMPILERS)
def test_own_export_removed_in_the_same_rebuild_still_breaks(tmp_path, cxx, evidence):
    cxx = _driver(cxx)
    debug = evidence != "binary"
    shrunk = '#include "edit_seed.h"\n' + _GREET + _ADD  # name_len no longer defined
    old = _snap(_build(tmp_path, "old", cxx, ["-O0"], _SOURCE, debug), evidence, [])
    new = _snap(_build(tmp_path, "new", cxx, ["-O2"], shrunk, debug), evidence, [])
    result = compare(old, new)
    assert result.verdict is Verdict.BREAKING
    broken = {sym for kind, sym in _not_compatible(result)}
    assert any("name_len" in s for s in broken), broken
    assert all("name_len" in s for s in broken if not s.startswith("__stack")), broken


def test_dual_abi_rebuild_refusal_names_the_macro(tmp_path):
    cxx = _driver("g++")
    define = "-D_GLIBCXX_USE_CXX11_ABI=0"
    old = _snap(_build(tmp_path, "old", cxx, ["-O0"], _SOURCE, True), "headers", [])
    new = _snap(
        _build(tmp_path, "new", cxx, ["-O0", define], _SOURCE, True),
        "headers",
        [define],
    )
    (tmp_path / "old.json").write_text(snapshot_to_json(old))
    (tmp_path / "new.json").write_text(snapshot_to_json(new))
    report = tmp_path / "out.json"
    run = CliRunner().invoke(
        main,
        [
            "compare",
            str(tmp_path / "old.json"),
            str(tmp_path / "new.json"),
            "-o",
            f"json={report}",
        ],
    )
    assert run.exit_code == 16, run.output
    doc = json.loads(report.read_text())
    assert doc["verdict"] is None
    assert doc["reason"]["kind"] == "profile_mismatch"
    message = doc["reason"]["message"]
    assert "only in new: -D_GLIBCXX_USE_CXX11_ABI=0" in message
    assert "--diagnostic-comparison" in message

    # The binaries alone report the rename the refusal withholds.
    bin_old = _snap(tmp_path / "old" / "libseed.so", "binary", [])
    bin_new = _snap(tmp_path / "new" / "libseed.so", "binary", [])
    assert compare(bin_old, bin_new).verdict is Verdict.BREAKING


def _require_m32() -> None:
    probe = subprocess.run(
        ["gcc", "-m32", "-shared", "-fPIC", "-x", "c", "-", "-o", "/dev/null"],
        input="int x;\n",
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        pytest.skip(
            f"gcc -m32 cannot link (gcc-multilib missing): {probe.stderr[-200:]}"
        )


@pytest.mark.parametrize("evidence", _EVIDENCE)
def test_i386_c_rebuild_leaves_only_the_stack_canary(tmp_path, evidence):
    """The corpus's minimized cell (rebuild_optimized on c_core, c17 gcc13 -m32).

    The address-placement alignment drop of ``g_counter`` (16 -> 8 in a
    4-aligned ``.bss``) is gone; ``-O2`` needs no canary, so the recorded
    ``stack_canary_removed`` RISK is all that is left, as the strict
    ``tests/_edit_corpus_known.py`` entry records.
    """
    _driver("gcc")
    _require_m32()
    from tests._edit_corpus_cells import CellContext, run_cell
    from tests._edit_corpus_config import STANDARD_BY_NAME, Config, Flags
    from tests._edit_corpus_known import known_mismatch
    from tests._edit_corpus_operators import OP_BY_NAME
    from tests._edit_corpus_seeds import SEED_BY_NAME
    from tests._edit_corpus_toolchains import toolchain

    config = Config(
        std=STANDARD_BY_NAME["c17"],
        producer=toolchain("gcc", 13),
        flags=Flags(bits=32),
        evidence=evidence,
    )
    cell = run_cell(
        OP_BY_NAME["rebuild_optimized"],
        SEED_BY_NAME["c_core"],
        config,
        CellContext.at(tmp_path),
    )
    risky = {
        k
        for k in cell.kinds
        if k.lower() in {c.value for c in RISK_KINDS | BREAKING_KINDS}
    }
    assert "EXPORTED_OBJECT_ALIGNMENT_REDUCED" not in cell.kinds, cell.describe()
    assert risky <= {"STACK_CANARY_REMOVED"}, cell.describe()
    known = known_mismatch("rebuild_optimized", "c_core", config)
    if risky:
        assert known is not None and cell.verdict in known.verdicts, cell.describe()
    else:
        assert cell.verdict in ("NO_CHANGE", "COMPATIBLE"), cell.describe()


def test_a_real_alignment_loss_still_reports(tmp_path):
    """Negative control: ``alignas(16)`` dropped from an exported object is a
    genuine reduction, and the section bound keeps it visible."""
    cxx = _driver("gcc")
    snaps = []
    for name, decl in (
        ("old", "_Alignas(16) int g_data = 1;"),
        ("new", "int g_data = 1;"),
    ):
        d = tmp_path / name
        d.mkdir()
        (d / "lib.c").write_text(
            f"int pad = 2;\n{decl}\nint get(void) {{ return g_data; }}\n"
        )
        so = d / "libal.so"
        cmd = [cxx, "-O0", "-shared", "-fPIC", "-o", str(so), str(d / "lib.c")]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        assert proc.returncode == 0, f"{cmd}\n{proc.stderr}"
        snaps.append(dump(so, [], compiler="cc", lang="c"))
    kinds = {c.kind.value for c in compare(*snaps).changes}
    assert "exported_object_alignment_reduced" in kinds, kinds
