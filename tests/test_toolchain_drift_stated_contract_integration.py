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

"""A toolchain-only rebuild never fails contract coverage -- compiled.

The same C API (``tests/fixtures/toolchain_drift/src``, built with
``-fvisibility=hidden``) is compiled twice with drift the SVS field report
had and no fixture did: a different ``-O``, an internal ``std::to_chars``/
iostream path only in the rebuild (so other libstdc++ entry points are
inlined or imported), and ``-static-libstdc++`` toggled. Each pair goes
through the public CLI under every contract mode, the stated ones included.

Invariant: the library's declarations did not change, so
``contract_coverage_failures`` is empty, the exit is 0 (``COMPATIBLE_WITH_RISK``
is exit 0 -- docs/reference/exit-codes.md), and the import edges the rebuild
added are still listed in the JSON report. A byte-identical pair is
``NO_CHANGE``, exit 0, under every mode.

Cells that do not hold yet are listed in :data:`KNOWN_GAP_CELLS` as strict
expected failures -- the list may only shrink: a fixed cell XPASSes and fails
until its entry is deleted. The gaps are written up in
``docs/contribute/known-gaps/toolchain-runtime-rebuild-fails-stated-contract-coverage.md``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not sys.platform.startswith("linux"), reason="ELF + libstdc++ drift fixture"
    ),
]

SRC_DIR = Path(__file__).parent / "fixtures" / "toolchain_drift" / "src"
HEADER = SRC_DIR / "svs.h"
SOURCE = SRC_DIR / "svs.cpp"

COMPILERS = ("g++", "clang++")
MODES = ("public", "exports", "all", "auto")

#: (old flags, new flags) per drift axis, plus every axis at once.
VARIANTS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "opt_level": (("-O0",), ("-O2",)),
    "inline_imports": (("-O0",), ("-O0", "-DDRIFT_V2")),
    "static_runtime": (("-O0",), ("-O0", "-static-libstdc++")),
    "combined": (("-O0",), ("-O2", "-DDRIFT_V2", "-static-libstdc++")),
}

#: Builds compared against a byte-identical copy of themselves.
IDENTICAL_BUILDS: dict[str, tuple[str, ...]] = {
    "O0": ("-O0",),
    "O2": ("-O2",),
    "static_runtime": ("-O0", "-static-libstdc++"),
}

#: Gap ids, written up in the known-gaps entry named in the module docstring.
#: ``tm_clone``: libstdc++'s transactional-memory clones (``_ZGTt...``),
#: re-exported by ``-static-libstdc++``, are not recognised as runtime-owned,
#: so a stated ``public`` leaves them UNKNOWN_UNRESOLVED.
#: ``unmatched_runtime_exports``: an exported libstdc++ symbol (a static
#: runtime, or clang's default-visibility ``std::`` instantiations) has no
#: declaration, so the stated ``exports`` domain is ``unmatched_exports`` and
#: cannot close -- even for a byte-identical pair.
_TM = "tm_clone"
_UNMATCHED = "unmatched_runtime_exports"

#: Shrink-only: (compiler, variant, mode) -> gap id. Observed on Ubuntu 24.04
#: (gcc 13, clang 18, libstdc++ 13).
KNOWN_GAP_CELLS: dict[tuple[str, str, str], str] = {
    ("g++", "static_runtime", "public"): _TM,
    ("g++", "static_runtime", "exports"): _UNMATCHED,
    ("g++", "combined", "public"): _TM,
    ("g++", "combined", "exports"): _UNMATCHED,
    ("clang++", "opt_level", "exports"): _UNMATCHED,
    ("clang++", "inline_imports", "exports"): _UNMATCHED,
    ("clang++", "static_runtime", "public"): _TM,
    ("clang++", "static_runtime", "exports"): _UNMATCHED,
    ("clang++", "combined", "public"): _TM,
    ("clang++", "combined", "exports"): _UNMATCHED,
}

#: Shrink-only: (compiler, identical build, mode) -> gap id.
KNOWN_GAP_IDENTICAL: dict[tuple[str, str, str], str] = {
    ("g++", "static_runtime", "exports"): _UNMATCHED,
    ("clang++", "O0", "exports"): _UNMATCHED,
    ("clang++", "static_runtime", "exports"): _UNMATCHED,
}


def _cells(gaps: dict[tuple[str, str, str], str], names: list[str]) -> list:
    params = []
    for compiler in COMPILERS:
        for name in names:
            for mode in MODES:
                key = (compiler, name, mode)
                marks = (
                    [pytest.mark.xfail(strict=True, reason=f"known gap: {gaps[key]}")]
                    if key in gaps
                    else []
                )
                params.append(pytest.param(*key, marks=marks, id="-".join(key)))
    return params


@pytest.fixture(scope="module")
def build(tmp_path_factory: pytest.TempPathFactory):
    """Compile (and cache) one shared library per (compiler, flags)."""
    out_dir = tmp_path_factory.mktemp("toolchain_drift")
    cache: dict[tuple[str, tuple[str, ...]], Path] = {}

    def _build(compiler: str, flags: tuple[str, ...]) -> Path:
        key = (compiler, flags)
        if key in cache:
            return cache[key]
        exe = shutil.which(compiler)
        if exe is None:
            pytest.skip(f"{compiler} not on PATH")
        lib = out_dir / f"lib{len(cache)}.so"
        cmd = [
            exe,
            "-shared",
            "-fPIC",
            "-g",
            "-fvisibility=hidden",
            "-fvisibility-inlines-hidden",
            *flags,
            "-o",
            str(lib),
            str(SOURCE),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        # A configured compiler that ran and failed is a broken fixture, not
        # an absent capability: fail, never skip.
        assert proc.returncode == 0, f"{' '.join(cmd)}\n{proc.stderr}"
        cache[key] = lib
        return lib

    return _build


def _compare(old: Path, new: Path, mode: str, report: Path) -> tuple[int, dict]:
    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(old),
            str(new),
            "-H",
            str(HEADER),
            "--contract",
            mode,
            "-o",
            f"json={report}",
        ],
    )
    assert report.is_file(), result.output
    return result.exit_code, json.loads(report.read_text(encoding="utf-8"))


def _strong_imports(lib: Path) -> set[str]:
    """Undefined, non-weak dynamic symbols, read by binutils -- an oracle
    independent of abicheck's own ELF reader."""
    out = subprocess.run(
        ["nm", "-D", "--undefined-only", str(lib)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    names = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-2] == "U":
            names.add(parts[-1].split("@", 1)[0])
    return names


def _recorded(report: dict) -> list[dict]:
    scope = report.get("scope") or {}
    return [*report["changes"], *scope.get("filtered_internal_changes", [])]


def _assert_coverage_clean(rc: int, report: dict) -> None:
    unresolved = sorted(
        {
            (f["kind"], f.get("symbol", ""))
            for f in _recorded(report)
            if f.get("contract_relevance") == "UNKNOWN_UNRESOLVED"
        }
    )
    assert report["contract_coverage_failures"] == [], unresolved[:10]
    assert unresolved == []
    assert rc == 0, report["exit"]


@pytest.mark.parametrize(
    ("compiler", "variant", "mode"), _cells(KNOWN_GAP_CELLS, list(VARIANTS))
)
def test_toolchain_drift_never_fails_contract_coverage(
    compiler: str, variant: str, mode: str, build, tmp_path: Path
) -> None:
    if not shutil.which("castxml"):
        pytest.skip("castxml not on PATH")
    old_flags, new_flags = VARIANTS[variant]
    old, new = build(compiler, old_flags), build(compiler, new_flags)
    rc, report = _compare(old, new, mode, tmp_path / "report.json")

    assert report["verdict"] in {"NO_CHANGE", "COMPATIBLE", "COMPATIBLE_WITH_RISK"}
    # The drift is still reported: every strong import the rebuild added is
    # listed, whichever domain the mode judges findings against.
    added_imports = _strong_imports(new) - _strong_imports(old)
    listed = {
        f["symbol"] for f in _recorded(report) if f["kind"] == "imported_symbol_added"
    }
    assert added_imports <= listed, sorted(added_imports - listed)[:10]
    _assert_coverage_clean(rc, report)


@pytest.mark.parametrize(
    ("compiler", "flavor", "mode"), _cells(KNOWN_GAP_IDENTICAL, list(IDENTICAL_BUILDS))
)
def test_byte_identical_build_is_no_change(
    compiler: str, flavor: str, mode: str, build, tmp_path: Path
) -> None:
    if not shutil.which("castxml"):
        pytest.skip("castxml not on PATH")
    lib = build(compiler, IDENTICAL_BUILDS[flavor])
    copy = tmp_path / "copy.so"
    shutil.copyfile(lib, copy)
    rc, report = _compare(lib, copy, mode, tmp_path / "report.json")
    # A persistent hygiene finding (``exported_not_public`` on a leaked
    # runtime symbol) may still be listed; nothing may read as a change.
    assert report["verdict"] == "NO_CHANGE"
    _assert_coverage_clean(rc, report)
