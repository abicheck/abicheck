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

"""Harness H8 -- target and toolchain parity for defect family F8.

The family: the same headers dumped for another target, or through another
toolchain's install layout, must parse and yield the same declaration surface.
Two escapes motivated it, both invisible to every x86-64 lane:

* ``extraction.emulated_compiler_builtin_absent_from_frontend`` -- castxml
  emulates GCC, so glibc takes branches that name GCC builtins castxml's Clang
  lacks on AArch64 (``_Float128``, ``__Float32x4_t``): every C++ header
  reaching ``<cwchar>`` and every header reaching ``<math.h>`` failed there.
* ``scoping.system_header_layout_unrecognized`` -- a cross toolchain's
  ``/usr/<triple>/include`` was not a system root, so a cross dump kept the
  target's whole libc/libstdc++ surface.
* ``extraction.linker_summary_flag_read_as_the_fact`` -- static-TLS use was
  read from ``DF_STATIC_TLS``, which GNU ld writes on x86-64 but not on
  AArch64, so the same initial-exec library had the fact on one target only.

**Artifact-fact cells.** The same TLS source under every ``-ftls-model``, built
for each target: ``has_static_tls`` must agree across targets wherever both
toolchains link the model (local-exec in a shared object is excluded where a
target's linker leaves no trace of it -- ``docs/contribute/known-gaps.md``).

**Matrix.** Header sets chosen to reach the target-specific GCC branches (C++
libstdc++, ``<cmath>``, C ``<math.h>``) x targets (the host, and AArch64 through
an installed ``aarch64-linux-gnu`` cross toolchain). Each cell is a real
``abicheck dump`` (CLI, real castxml).

**Oracle.** The *library-owned* surface -- every function and record declared
in the cell's own header, with its type spellings -- is identical across
targets, and the whole dump stays within a bound of that surface (no
target-system declarations retained). Written against the snapshot through
``load_snapshot``, never the code paths under test.

**Seeded mutants.** Each family bug is patched back in (in the dump
subprocess, via ``sitecustomize``) and the oracle must report it: an emptied
compatibility preamble (AArch64 cells stop parsing) and a removed cross-sysroot
recognizer (the AArch64 surface balloons).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from abicheck.serialization import load_snapshot

_CASTXML = shutil.which("castxml")
_CROSS = {
    "aarch64": ("aarch64-linux-gnu-gcc", "aarch64-linux-gnu-g++"),
}


@dataclass(frozen=True)
class HeaderSet:
    name: str
    lang: str  # "c" | "c++"
    includes: tuple[str, ...]


HEADER_SETS = (
    HeaderSet("cxx_stdlib", "c++", ("string", "cwchar", "cstdlib", "memory", "map")),
    HeaderSet("cxx_cmath", "c++", ("cmath", "complex", "limits")),
    HeaderSet("c_math", "c", ("math.h", "wchar.h", "stdlib.h")),
)

#: A dump of these tiny headers keeps at most this many functions; the
#: target's libc/libstdc++ surface is thousands.
_SURFACE_BOUND = 50


def _available_targets() -> list[str]:
    return [
        t for t, (cc, cxx) in _CROSS.items() if shutil.which(cc) and shutil.which(cxx)
    ]


def _write_case(tmp: Path, hs: HeaderSet) -> tuple[Path, Path]:
    ext = "hpp" if hs.lang == "c++" else "h"
    header = tmp / f"api.{ext}"
    body = (
        "struct S { int a; double d; };\nint f(const struct S* s);\ndouble g(double);\n"
    )
    header.write_text(
        "#pragma once\n" + "".join(f"#include <{i}>\n" for i in hs.includes) + body
    )
    src = tmp / ("api.cpp" if hs.lang == "c++" else "api.c")
    src.write_text(
        f'#include "api.{ext}"\nint f(const struct S* s) {{ return s->a; }}\ndouble g(double x) {{ return x; }}\n'
    )
    return header, src


def _shim(tmp: Path, target: str | None) -> Path | None:
    if target is None:
        return None
    shim = tmp / f"shim-{target}"
    shim.mkdir(exist_ok=True)
    cc, cxx = _CROSS[target]
    for tool, real in (("gcc", cc), ("g++", cxx), ("cc", cc), ("c++", cxx)):
        if not (shim / tool).exists():
            (shim / tool).symlink_to(shutil.which(real))
    return shim


def _dump(tmp: Path, hs: HeaderSet, target: str | None, mutant: str | None = None):
    """One cell: build, dump through the real CLI; the loaded snapshot or None."""
    work = tmp / f"{hs.name}-{target or 'host'}-{mutant or 'real'}"
    work.mkdir()
    header, src = _write_case(work, hs)
    shim = _shim(tmp, target)
    compiler = (
        str(shim / ("g++" if hs.lang == "c++" else "gcc"))
        if shim
        else ("g++" if hs.lang == "c++" else "gcc")
    )
    lib = work / "libapi.so"
    subprocess.run([compiler, "-shared", "-fPIC", str(src), "-o", str(lib)], check=True)
    env = {**os.environ, "XDG_CACHE_HOME": str(work / "cache")}
    if shim:
        env["PATH"] = f"{shim}{os.pathsep}{env['PATH']}"
    if mutant:
        site = work / "site"
        site.mkdir()
        (site / "sitecustomize.py").write_text(
            _MUTANTS[mutant]
            + f"\nimport pathlib\npathlib.Path({str(work / 'mutant-ran')!r}).touch()\n"
        )
        env["PYTHONPATH"] = f"{site}{os.pathsep}{env.get('PYTHONPATH', '')}"
    out = work / "snap.json"
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "abicheck",
            "dump",
            str(lib),
            "-H",
            str(header),
            "-o",
            str(out),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    if mutant:
        assert (work / "mutant-ran").exists(), (
            "the seeded mutant never loaded; the check would be vacuous"
        )
    return load_snapshot(out) if r.returncode == 0 else None


def _owned_surface(snap) -> frozenset[tuple[str, str]]:
    funcs = {
        ("fn", f"{f.name}({','.join(p.type for p in f.params)})->{f.return_type}")
        for f in snap.declarations.functions
        if f.name in ("f", "g")
    }
    recs = {
        ("rec", f"{t.name}:{','.join(x.type for x in t.fields)}")
        for t in snap.declarations.types
        if t.name == "S"
    }
    return frozenset(funcs | recs)


def parity_violations(host, other) -> list[str]:
    """The oracle: what keeps *other* from matching *host*'s owned surface."""
    if other is None:
        return ["dump failed on the target"]
    out = []
    if _owned_surface(other) != _owned_surface(host):
        out.append(
            f"owned surface differs: {sorted(_owned_surface(host) ^ _owned_surface(other))}"
        )
    if len(other.declarations.functions) > _SURFACE_BOUND:
        out.append(
            f"{len(other.declarations.functions)} functions kept (target system surface retained)"
        )
    return out


_MUTANTS = {
    # extraction.emulated_compiler_builtin_absent_from_frontend, reverted.
    "no_preamble": "import abicheck.extract.castxml_header_compat as c\nc.CASTXML_HEADER_PREAMBLE = ''\n",
    # extraction.linker_summary_flag_read_as_the_fact, reverted (in-process).
    # scoping.system_header_layout_unrecognized, reverted.
    "no_cross_sysroot": (
        "import abicheck.extract.system_header_layout as l, abicheck.provenance as p\n"
        "l._cross_sysroot_includes = p._cross_sysroot_includes = lambda segs: []\n"
    ),
}

_needs_tools = pytest.mark.skipif(
    not (
        _CASTXML
        and shutil.which("gcc")
        and shutil.which("g++")
        and _available_targets()
    ),
    reason="needs castxml, gcc/g++ and an aarch64-linux-gnu cross toolchain",
)


@pytest.mark.integration
@_needs_tools
@pytest.mark.parametrize("hs", HEADER_SETS, ids=lambda h: h.name)
@pytest.mark.parametrize("target", _available_targets() or ["aarch64"])
def test_target_parity(hs: HeaderSet, target: str, tmp_path: Path) -> None:
    host = _dump(tmp_path, hs, None)
    assert host is not None, "the host-target dump must succeed"
    assert _owned_surface(host), "vacuity guard: the owned surface is non-empty"
    assert not parity_violations(host, host)
    assert not parity_violations(host, _dump(tmp_path, hs, target))


@pytest.mark.integration
@_needs_tools
def test_seeded_mutant_missing_builtin_preamble_is_reported(tmp_path: Path) -> None:
    hs = HEADER_SETS[0]
    host = _dump(tmp_path, hs, None)
    assert parity_violations(host, _dump(tmp_path, hs, "aarch64", mutant="no_preamble"))


@pytest.mark.integration
@_needs_tools
def test_seeded_mutant_unrecognized_cross_sysroot_is_reported(tmp_path: Path) -> None:
    hs = HEADER_SETS[2]  # C: parses without the preamble, so only scoping differs
    host = _dump(tmp_path, hs, None)
    found = parity_violations(
        host, _dump(tmp_path, hs, "aarch64", mutant="no_cross_sysroot")
    )
    assert any("target system surface retained" in v for v in found), found


# -- Artifact facts: static TLS per TLS model, per target ---------------------

_TLS_SRC = "__thread int counter;\nint bump(void) { return ++counter; }\n"
_TLS_MODELS = ("global-dynamic", "local-dynamic", "initial-exec")


def _static_tls_by_target(tmp: Path) -> dict[tuple[str, str], bool]:
    from abicheck.elf_metadata import parse_elf_metadata

    src = tmp / "tls.c"
    src.write_text(_TLS_SRC)
    facts: dict[tuple[str, str], bool] = {}
    for target, cc in (
        ("host", "gcc"),
        *((t, _CROSS[t][0]) for t in _available_targets()),
    ):
        for model in _TLS_MODELS:
            lib = tmp / f"lib-{target}-{model}.so"
            subprocess.run(
                [
                    cc,
                    "-shared",
                    "-fPIC",
                    "-O1",
                    f"-ftls-model={model}",
                    str(src),
                    "-o",
                    str(lib),
                ],
                check=True,
            )
            facts[(target, model)] = parse_elf_metadata(lib).has_static_tls
    return facts


def tls_parity_violations(facts: dict[tuple[str, str], bool]) -> list[str]:
    out = []
    for model in _TLS_MODELS:
        values = {t: v for (t, m), v in facts.items() if m == model}
        if len(set(values.values())) > 1:
            out.append(f"{model}: {values}")
    return out


_tls_tools = pytest.mark.skipif(
    not (shutil.which("gcc") and _available_targets()),
    reason="needs gcc and an aarch64-linux-gnu cross toolchain",
)


@pytest.mark.integration
@_tls_tools
def test_static_tls_fact_parity_across_targets(tmp_path: Path) -> None:
    facts = _static_tls_by_target(tmp_path)
    # Vacuity guard: the matrix holds both answers, so agreement is not trivial.
    assert set(facts.values()) == {True, False}, facts
    assert not tls_parity_violations(facts)


@pytest.mark.integration
@_tls_tools
def test_seeded_mutant_flag_only_static_tls_is_reported(
    tmp_path: Path, monkeypatch
) -> None:
    import abicheck.elf_metadata as em

    monkeypatch.setattr(em, "has_static_tls_relocation", lambda elf: False)
    assert tls_parity_violations(_static_tls_by_target(tmp_path))
