# Copyright 2026 Nikolay Petrov
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

"""Integration: real kernel-style BTF extracted from an ELF `.BTF` section (G6).

Complements the pure-Python committed-blob fixture (examples/case121,
tests/test_workflow_kernel_accel.py) with a *real* `.BTF` section produced by
the toolchain. GCC's ``-gbtf`` emits BTF directly (the same section `pahole -J`
embeds into ``vmlinux``); this drives the canonical "module vs vmlinux BTF"
struct-layout break through ``compare`` on native binaries.

Requires Linux + a GCC new enough to support ``-gbtf`` (GCC 12+); skipped
otherwise.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from abicheck.btf_metadata import has_btf_section, parse_btf_metadata
from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.model import AbiSnapshot

pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="BTF emission via gcc -gbtf is Linux-only",
)


def _compile_btf_so(src: str, name: str, tmp: Path) -> Path:
    """Compile a shared library with a real `.BTF` section, or skip."""
    out = tmp / name
    cmd = ["gcc", "-gbtf", "-shared", "-fPIC", "-o", str(out), "-x", "c", "-"]
    result = subprocess.run(cmd, input=src.encode(), capture_output=True)
    if result.returncode != 0:
        pytest.skip(f"gcc -gbtf unsupported: {result.stderr.decode()[:200]}")
    if not has_btf_section(out):
        pytest.skip("gcc produced no .BTF section")
    return out


def _btf_snapshot(so: Path, version: str) -> AbiSnapshot:
    meta = parse_btf_metadata(so)
    return AbiSnapshot(library=so.name, version=version, dwarf=meta.to_dwarf_metadata())


@pytest.mark.integration
def test_real_btf_struct_growth_is_breaking() -> None:
    """A struct that grows a field across builds is BREAKING via the BTF path."""
    v1 = "struct task_state { int f0; int f1; };\n" \
         "struct task_state *use(struct task_state *p) { return p; }\n"
    v2 = "struct task_state { int f0; int f1; int f2; };\n" \
         "struct task_state *use(struct task_state *p) { return p; }\n"
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        old = _btf_snapshot(_compile_btf_so(v1, "libbtf_v1.so", tmp), "1")
        new = _btf_snapshot(_compile_btf_so(v2, "libbtf_v2.so", tmp), "2")

    result = compare(old, new)
    assert result.verdict is Verdict.BREAKING
    assert ChangeKind.STRUCT_SIZE_CHANGED in {c.kind for c in result.changes}


@pytest.mark.integration
def test_real_btf_identical_is_not_breaking() -> None:
    src = "struct task_state { int f0; int f1; };\n" \
          "struct task_state *use(struct task_state *p) { return p; }\n"
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        old = _btf_snapshot(_compile_btf_so(src, "libbtf_a.so", tmp), "1")
        new = _btf_snapshot(_compile_btf_so(src, "libbtf_b.so", tmp), "2")

    result = compare(old, new)
    assert result.verdict is not Verdict.BREAKING
