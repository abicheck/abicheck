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

"""Known-gap canaries for binary, DWARF, castxml-mangling and contract gaps.

Each test asserts the *current, gapped* answer of one open
`docs/contribute/known-gaps.md` entry; the entry names the test with a
``<!-- canary: ... -->`` marker and the `known-gap-canaries` gate checks
the link both ways. Most drive the public `dump`/`compare` CLI on libraries
compiled here.

When one of these fails, the gap moved. If it was fixed, move the entry to
`docs/contribute/archive/known-gaps-closed.md`, turn the test into a
regression test of the correct answer (drop `known_gap_canary`) and remove
the entry's marker. If it got worse, fix the regression.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from abicheck.errors import SnapshotError
from abicheck.extract.declaration_provenance import _is_toolchain_compiler_include_dir
from abicheck.serialization import load_snapshot, snapshot_to_dict
from tests._abicheck_subprocess import abicheck_command, abicheck_env

pytestmark = pytest.mark.known_gap_canary

#: Every compiled canary pins the answer measured with gcc/castxml/clang on a
#: Linux ELF host; on Windows or macOS the toolchain, binary format and so the
#: gapped answer differ, so the canary would assert the wrong bound there.
_LINUX_ONLY = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="pins the gap as measured on Linux/ELF"
)

_ROOT = Path(__file__).resolve().parents[1]


def _require(*tools: str) -> None:
    for tool in tools:
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not installed")


def _build(
    side: Path, source: str, *, lang: str, flags: list[str], header: str | None = None
) -> tuple[Path, Path | None]:
    side.mkdir(parents=True)
    src = side / ("api.c" if lang == "c" else "api.cpp")
    src.write_text(source, encoding="utf-8")
    hdr = None
    if header is not None:
        hdr = side / "api.h"
        hdr.write_text(header, encoding="utf-8")
    lib = side / "lib.so"
    compiler = ["gcc"] if lang == "c" else ["g++", "-std=c++17"]
    subprocess.run(
        [*compiler, *flags, "-shared", "-fPIC", f"-I{side}", str(src), "-o", str(lib)],
        check=True,
    )
    return lib, hdr


def _dump(lib: Path, header: Path | None = None, *, lang: str = "c++") -> Path:
    out = lib.with_suffix(".json")
    args: list[Any] = ["dump", lib, "-o", out]
    if header is not None:
        cfg = lib.parent / "abicheck.yml"
        cfg.write_text(
            f"compile:\n  frontend: castxml\n  lang: {lang}\n", encoding="utf-8"
        )
        args += ["-H", header, "--config", cfg]
    run = subprocess.run(
        abicheck_command(*args),
        capture_output=True,
        text=True,
        env=abicheck_env(),
        check=False,
    )
    assert run.returncode == 0, run.stderr
    return out


def _compare(old: Path, new: Path) -> dict[str, Any]:
    report = old.parent.parent / "report.json"
    subprocess.run(
        abicheck_command("compare", old, new, "-o", f"json={report}"),
        capture_output=True,
        env=abicheck_env(),
        check=False,
    )
    return json.loads(report.read_text(encoding="utf-8"))


def _pair(
    tmp: Path,
    sources: tuple[str, str],
    *,
    lang: str,
    flags: list[str],
    headers: tuple[str, str] | None = None,
) -> dict[str, Any]:
    snaps = []
    for i, tag in enumerate(("old", "new")):
        lib, hdr = _build(
            tmp / tag,
            sources[i],
            lang=lang,
            flags=flags,
            header=headers[i] if headers else None,
        )
        snaps.append(_dump(lib, hdr, lang="c" if lang == "c" else "c++"))
    return _compare(*snaps)


def _kinds(report: dict[str, Any]) -> set[str]:
    return {c["kind"] for c in report["changes"]}


# -- "`debug = 1` DWARF has no function types, so a return-type break reads
#    NO_CHANGE with assurance: complete" (reproduced with C and gcc -g1)


@pytest.mark.integration
@_LINUX_ONLY
def test_g1_dwarf_return_type_change_reads_complete_no_change(tmp_path: Path) -> None:
    """Line-tables-only DWARF has no `DW_AT_type`: the return change is missed, assurance complete."""
    _require("gcc")
    report = _pair(
        tmp_path,
        ("int f(int x) { return x; }\n", "long f(int x) { return x; }\n"),
        lang="c",
        flags=["-g1"],
    )
    assert "func_return_changed" not in _kinds(report)
    assert (report["verdict"], report["analysis_assurance"]["status"]) == (
        "NO_CHANGE",
        "complete",
    )


# -- "Header-less runs flag exported_object_alignment_reduced on an unrelated
#    exported static after an additive change"


@pytest.mark.integration
@_LINUX_ONLY
def test_headerless_additive_change_flags_alignment_on_an_untouched_static(
    tmp_path: Path,
) -> None:
    """The alignment comes from the link-time address, so a neighbour's addition moves it."""
    _require("gcc")
    old = "int counter = 1;\nint f(void) { return counter; }\n"
    new = old + "int g(void) { return 2; }\nchar extra[3] = {1, 2, 3};\n"
    report = _pair(tmp_path, (old, new), lang="c", flags=["-O2"])
    flagged = [
        c for c in report["changes"] if c["kind"] == "exported_object_alignment_reduced"
    ]
    assert [c["symbol"] for c in flagged] == ["counter"]
    assert report["verdict"] == "COMPATIBLE_WITH_RISK"


# -- "A typedef retargeted inside a template argument still reads as a
#    removal plus an addition under headers"

_TD = "#include <vector>\ntypedef {target} T;\nvoid f(std::vector<T> v);\n"
_TD_SRC = '#include "api.h"\nvoid f(std::vector<T>) {}\n'


@pytest.mark.integration
@_LINUX_ONLY
def test_typedef_retarget_inside_template_argument_reads_removed_plus_added(
    tmp_path: Path,
) -> None:
    _require("castxml", "g++")
    report = _pair(
        tmp_path,
        (_TD_SRC, _TD_SRC),
        lang="c++",
        flags=["-g"],
        headers=(_TD.format(target="int"), _TD.format(target="long")),
    )
    kinds = _kinds(report)
    assert {"func_removed", "func_added"} <= kinds
    assert "func_params_changed" not in kinds


# -- "A library's own `_Float128` API has no correct mangled name from castxml"


@pytest.mark.integration
@_LINUX_ONLY
def test_castxml_float128_function_is_unmangled_and_read_as_not_exported(
    tmp_path: Path,
) -> None:
    _require("castxml", "g++")
    lib, hdr = _build(
        tmp_path / "lib",
        '#include "api.h"\n_Float128 fq(_Float128 x) { return x; }\n',
        lang="c++",
        flags=["-g"],
        header="_Float128 fq(_Float128 x);\n",
    )
    exported = subprocess.run(
        ["nm", "-D", str(lib)], capture_output=True, text=True, check=True
    )
    assert "_Z2fqDF128_" in exported.stdout  # the control: the binary exports it
    snap = load_snapshot(_dump(lib, hdr))
    fq = [f for f in snap.declarations.functions if f.name == "fq"]
    assert [(f.mangled, f.visibility.value) for f in fq] == [("fq", "hidden")]


# -- "Explicit function and variable instances: removal is castxml-blind"


@pytest.mark.integration
@_LINUX_ONLY
def test_castxml_dropped_explicit_function_instance_is_not_a_removal(
    tmp_path: Path,
) -> None:
    _require("castxml", "g++")
    decl = "template <class T> T g(T x);\nextern template int g<int>(int);\n"
    body = '#include "api.h"\ntemplate <class T> T g(T x) { return x; }\ntemplate int g<int>(int);\n'
    report = _pair(
        tmp_path,
        (body + "template long g<long>(long);\n", body),
        lang="c++",
        flags=["-g"],
        headers=(decl + "extern template long g<long>(long);\n", decl),
    )
    kinds = _kinds(report)
    assert "func_visibility_changed" in kinds
    assert "func_removed" not in kinds


# -- "An exported instantiation of a public class template is not in the
#    public contract" (case180)


@pytest.mark.integration
@_LINUX_ONLY
def test_case180_binding_loss_is_filtered_as_exported_not_public(
    tmp_path: Path,
) -> None:
    _require("castxml", "g++")
    root = _ROOT / "catalog" / "cases" / "case180_symbol_binding_lost_unique"
    snaps = []
    for tag, extra in (("v1", []), ("v2", ["-fno-gnu-unique"])):
        side = tmp_path / tag
        side.mkdir()
        lib = side / "lib.so"
        subprocess.run(
            [
                "g++",
                "-std=c++17",
                "-g",
                *extra,
                "-shared",
                "-fPIC",
                f"-I{root}",
                str(root / f"{tag}.cpp"),
                "-o",
                str(lib),
            ],
            check=True,
        )
        snaps.append(_dump(lib, root / f"{tag}.hpp"))
    report = _compare(*snaps)
    kinds = _kinds(report)
    assert (
        "symbol_binding_lost_unique" not in kinds
    )  # truth: COMPATIBLE_WITH_RISK with it
    owner = [
        c
        for c in report["changes"]
        if c["symbol"] == "_ZN8RegistryI6WidgetE8instanceEv"
    ]
    assert [c["kind"] for c in owner] == ["exported_not_public"]
    assert report["verdict"] == "NO_CHANGE"


# -- "A reloaded binary snapshot written since ADR-079 cannot be flattened
#    with snapshot_to_dict"


@pytest.mark.integration
@_LINUX_ONLY
def test_reloaded_binary_snapshot_refuses_to_flatten(tmp_path: Path) -> None:
    _require("gcc")
    lib, _ = _build(
        tmp_path / "lib", "int f(int x) { return x; }\n", lang="c", flags=["-g"]
    )
    snap = load_snapshot(_dump(lib))
    with pytest.raises(SnapshotError, match=r"header extensions on \['binary'\]"):
        snapshot_to_dict(snap)


# -- "MinGW toolchain headers are not classified as dependencies"


@pytest.mark.parametrize(
    ("path", "recognized"),
    [
        # control: the GCC private tree of the same MinGW install is recognized
        ("opt/mingw64/lib/gcc/x86_64-w64-mingw32/13.2.0/include/stddef.h", True),
        # the gap: MinGW's own <prefix>/<triple>/include tree is not
        ("opt/mingw64/x86_64-w64-mingw32/include/stdio.h", False),
        ("msys64/ucrt64/x86_64-w64-mingw32/include/windows.h", False),
    ],
)
def test_mingw_triple_include_tree_is_not_a_toolchain_dir(
    path: str, recognized: bool
) -> None:
    assert _is_toolchain_compiler_include_dir(tuple(path.split("/"))) is recognized


# -- "A binary-only dump loses exports with no debug subprogram, so a
#    still-exported symbol reads as removed"


@pytest.mark.integration
@_LINUX_ONLY
def test_binary_only_dump_drops_an_export_compiled_without_debug_info(
    tmp_path: Path,
) -> None:
    _require("gcc")
    tmp_path.joinpath("a.c").write_text(
        "int dbg(int x) { return x; }\n", encoding="utf-8"
    )
    tmp_path.joinpath("b.c").write_text(
        "int nodbg(int x) { return x + 1; }\n", encoding="utf-8"
    )
    objs = []
    for name, flags in (("a", ["-g"]), ("b", [])):
        obj = tmp_path / f"{name}.o"
        subprocess.run(
            ["gcc", *flags, "-c", "-fPIC", str(tmp_path / f"{name}.c"), "-o", str(obj)],
            check=True,
        )
        objs.append(str(obj))
    (tmp_path / "full").mkdir()
    (tmp_path / "mixed").mkdir()
    full = tmp_path / "full" / "lib.so"
    mixed = tmp_path / "mixed" / "lib.so"
    subprocess.run(
        [
            "gcc",
            "-g",
            "-shared",
            "-fPIC",
            str(tmp_path / "a.c"),
            str(tmp_path / "b.c"),
            "-o",
            str(full),
        ],
        check=True,
    )
    subprocess.run(["gcc", "-shared", *objs, "-o", str(mixed)], check=True)
    exported = subprocess.run(
        ["nm", "-D", str(mixed)], capture_output=True, text=True, check=True
    )
    assert " T nodbg" in exported.stdout  # the control: still exported
    mixed_snap = _dump(mixed)
    assert sorted(f.name for f in load_snapshot(mixed_snap).declarations.functions) == [
        "dbg"
    ]
    report = _compare(_dump(full), mixed_snap)
    removed = [c["symbol"] for c in report["changes"] if c["kind"] == "func_removed"]
    assert removed == ["nodbg"]


# -- "`--depth binary` on a header-built snapshot misses debug-only
#    declaration findings"


@pytest.mark.integration
@_LINUX_ONLY
def test_depth_binary_projection_of_header_snapshots_misses_a_return_change(
    tmp_path: Path,
) -> None:
    """A binary-only dump of the same pair reads BREAKING (`func_return_changed`)."""
    _require("castxml", "g++")
    snaps = []
    for tag, ret in (("old", "int"), ("new", "long")):
        lib, hdr = _build(
            tmp_path / tag,
            f'#include "api.h"\n{ret} f(int x) {{ return x; }}\n',
            lang="c++",
            flags=["-g"],
            header=f"{ret} f(int x);\n",
        )
        snaps.append(_dump(lib, hdr))
    report = tmp_path / "projected.json"
    subprocess.run(
        abicheck_command(
            "compare", *snaps, "--depth", "binary", "-o", f"json={report}"
        ),
        capture_output=True,
        env=abicheck_env(),
        check=False,
    )
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["changes"] == []
    assert (data["verdict"], data["analysis_assurance"]["status"]) == (
        "NO_CHANGE",
        "partial",
    )
    assert any(
        n.startswith(
            "not assessed at depth binary: debug-info function/variable signatures"
        )
        for n in data["analysis_assurance"]["notes"]
    )
