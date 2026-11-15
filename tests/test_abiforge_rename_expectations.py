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

"""Rename operators require only deterministic kinds, at every toolchain.

``FUNC_LIKELY_RENAMED`` comes from a size-dependent fingerprint match, and
functions under the 8-byte floor are never fingerprinted. The rename operators
must therefore not *require* it: their expectations are checked here across
toolchain families, versions and optimisation levels, so a required heuristic
kind cannot come back through one configuration.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from abiforge.model.config import (
    STANDARD_BY_NAME,
    Config,
    Flags,
    Toolchain,
    default_config,
)
from abiforge.operators import OP_BY_NAME

HEURISTIC_KINDS = frozenset({"FUNC_LIKELY_RENAMED"})
RENAME_OPS = ("inline_namespace_bumped", "api_version_suffix_bumped")
TOOLCHAINS = (
    Toolchain("gcc", 14, "gcc-14", "g++-14"),
    Toolchain("clang", 19, "clang-19", "clang++-19"),
    Toolchain("clang", 18, "clang-18", "clang++-18"),
)
OPT_LEVELS = ("O0", "O2")
EVIDENCE = ("binary", "headers")


def _config(toolchain: Toolchain, opt: str, evidence: str) -> Config:
    base = default_config("cxx", toolchain)
    return replace(
        base,
        std=STANDARD_BY_NAME["c++17"],
        flags=Flags(opt=opt),
        evidence=evidence,
        platform=("linux", "elf"),
    )


@pytest.mark.parametrize("op_name", RENAME_OPS)
@pytest.mark.parametrize("toolchain", TOOLCHAINS, ids=lambda t: t.id)
@pytest.mark.parametrize("opt", OPT_LEVELS)
@pytest.mark.parametrize("evidence", EVIDENCE)
def test_rename_operator_requires_no_heuristic_kind(
    op_name: str, toolchain: Toolchain, opt: str, evidence: str
) -> None:
    exp = OP_BY_NAME[op_name].expect(_config(toolchain, opt, evidence))
    assert not (exp.expected & HEURISTIC_KINDS), (
        f"{op_name} requires {sorted(exp.expected & HEURISTIC_KINDS)} on "
        f"{toolchain.id} {opt} {evidence}"
    )


@pytest.mark.parametrize("op_name", RENAME_OPS)
def test_rename_operator_expectation_is_toolchain_independent(op_name: str) -> None:
    # The same required kinds whatever the toolchain or optimisation level,
    # at each evidence level: the expectation names a fact, not a size.
    seen = {
        (evidence, exp.expected)
        for toolchain in TOOLCHAINS
        for opt in OPT_LEVELS
        for evidence in EVIDENCE
        for exp in [OP_BY_NAME[op_name].expect(_config(toolchain, opt, evidence))]
    }
    by_evidence: dict[str, set[frozenset[str]]] = {}
    for evidence, kinds in seen:
        by_evidence.setdefault(evidence, set()).add(kinds)
    for evidence, kinds in by_evidence.items():
        assert len(kinds) == 1, (op_name, evidence, kinds)


#: Compilers for the body-size check. A compiler missing from the host is a
#: marker-level skip (integration); a compiler that runs and fails is a failure.
SIZE_COMPILERS = ("gcc-14", "clang-18", "clang-19")


@pytest.mark.integration
@pytest.mark.parametrize("compiler", SIZE_COMPILERS)
@pytest.mark.parametrize("opt", OPT_LEVELS)
@pytest.mark.parametrize("std", ("c89", "c17"))
def test_prefix_rename_body_is_above_the_fingerprint_floor(
    tmp_path: Path, compiler: str, opt: str, std: str
) -> None:
    """The renamed function's body stays above ``_MIN_SYMBOL_SIZE``.

    ``symbols_prefixed_batch`` requires FUNC_LIKELY_RENAMED on binary
    evidence, which the fingerprint detector can only produce for a function
    at or above the size floor. The body is measured here, on the real
    compiler, so the requirement cannot silently drop below the floor.
    """
    import shutil
    import subprocess
    import sys

    from elftools.elf.elffile import ELFFile

    from abicheck.binary_fingerprint import _MIN_SYMBOL_SIZE
    from abiforge.operators import OP_BY_NAME

    # ELFFile reads ELF output only: a named compiler on macOS emits Mach-O.
    if sys.platform != "linux":
        pytest.skip("the size check reads ELF output; it runs on Linux hosts")
    if shutil.which(compiler) is None:
        pytest.skip(f"{compiler} is not installed on this host")
    _, source = OP_BY_NAME["symbols_prefixed_batch"].edit(
        {"elf.ren": ("int mylib_r_init(void);\nint mylib_r_run(int n);", "")}
    )["elf.ren"]
    src = tmp_path / "ren.c"
    src.write_text(source, encoding="utf-8")
    lib = tmp_path / f"ren_{compiler}_{opt}_{std}.so"
    subprocess.run(
        [
            compiler,
            "-shared",
            "-fPIC",
            f"-std={std}",
            f"-{opt}",
            "-o",
            str(lib),
            str(src),
        ],
        check=True,
    )
    with lib.open("rb") as fh:
        dynsym = ELFFile(fh).get_section_by_name(".dynsym")
        sizes = {
            sym.name: sym["st_size"]
            for sym in dynsym.iter_symbols()
            if sym.name.startswith("mylib_r_")
        }
    assert set(sizes) == {"mylib_r_init", "mylib_r_run"}, sizes
    assert sizes["mylib_r_run"] > _MIN_SYMBOL_SIZE, (compiler, opt, sizes)
