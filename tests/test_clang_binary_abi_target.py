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

"""The clang header frontend parses for the C++ ABI the binary was built with.

Bug class ``extraction.header_dialect_not_recovered_from_binary``. A Windows
clang defaults to ``*-windows-msvc``. Parsing a MinGW (Itanium-ABI) DLL's
headers for that target mangled every declaration MSVC-style, none matched an
export, and ``--ast-frontend clang`` reported nothing for the whole surface
(13 cells of ``test_one_change_one_finding_matrix`` on windows-latest).

* an exhaustive small domain over clang's default triple x the exports' ABI x
  an explicit target x the GNU probe compiler's triple, against a rule table
  written here (not the implementation's helpers);
* the include-probe rule the injected target relies on: a ``--target`` naming
  the probe compiler's own triple keeps its system include dirs, any other
  target still suppresses them;
* end to end, on any host with clang + lld: a ``clang`` wrapper whose default
  is ``x86_64-pc-windows-msvc`` stands in for a Windows clang, a real
  windows-gnu DLL is compared through the CLI under ``ABICHECK_AST_FRONTEND=
  clang``, and the struct change must be reported.
"""

from __future__ import annotations

import itertools
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

import abicheck.extract.headers.clang.abi_target as abi_target
from abicheck.extract.headers import sysinc

_DEFAULTS = {
    "x86_64-pc-windows-msvc": ("x86_64", "msvc"),
    "x86_64-w64-windows-gnu": ("x86_64", "gnu"),
    "aarch64-pc-windows-msvc": ("aarch64", "msvc"),
    "x86_64-pc-linux-gnu": ("x86_64", None),
    "arm64-apple-macosx14.0.0": ("arm64", None),
}
_EXPORTS = {
    "itanium": ("_Z4keepi", "plain_c"),
    "itanium32": ("__Z4keepi",),
    "msvc": ("?keep@@YAHH@Z", "plain_c"),
    "c_only": ("plain_c", "other_c"),
    "mixed": ("_Z4keepi", "?keep@@YAHH@Z"),
}
_PROBES = (None, "x86_64-w64-mingw32", "x86_64-pc-linux-gnu", "i686-w64-mingw32")
_EXPLICIT = ((), ("--target=riscv64-unknown-linux-gnu",))


def _oracle(
    default: str, exports: str, explicit: bool, probe: str | None
) -> str | None:
    """The rule, stated from the problem: change only a Windows default whose
    ABI disagrees with what the exports are mangled for, and only when the
    caller named no target. Keep the default's architecture."""
    arch, env = _DEFAULTS[default]
    stated = {"itanium": "gnu", "itanium32": "gnu", "msvc": "msvc"}.get(exports)
    if explicit or env is None or stated is None or stated == env:
        return None
    if stated == "msvc":
        return f"--target={arch}-pc-windows-msvc"
    if probe is not None and probe.startswith(f"{arch}-") and "mingw" in probe:
        return f"--target={probe}"
    return f"--target={arch}-w64-windows-gnu"


@pytest.mark.parametrize(
    ("default", "exports", "explicit", "probe"),
    list(itertools.product(_DEFAULTS, _EXPORTS, _EXPLICIT, _PROBES)),
)
def test_target_follows_the_binary_abi(
    default: str,
    exports: str,
    explicit: tuple[str, ...],
    probe: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        abi_target, "_resolved_tool", lambda exe: (exe, Path(exe), None, "d")
    )
    monkeypatch.setattr(abi_target, "_default_triple", lambda sel, dig: default)
    monkeypatch.setattr(abi_target, "_tool_target_triple", lambda sel, dig: probe)
    got = abi_target.binary_abi_target_token(
        clang_bin="clang++",
        exported=_EXPORTS[exports],
        gcc_options=None,
        gcc_option_tokens=explicit,
        probe_cc="g++" if probe is not None else None,
    )
    assert got == _oracle(default, exports, bool(explicit), probe)


@pytest.mark.parametrize(
    ("tokens", "own", "suppressed"),
    [
        (("--target=x86_64-w64-mingw32",), "x86_64-w64-mingw32", False),
        (("--target=x86_64-w64-mingw32",), "x86_64-pc-linux-gnu", True),
        (("--target=aarch64-linux-gnu",), "x86_64-w64-mingw32", True),
        (("--target=x86_64-w64-mingw32", "--sysroot=/x"), "x86_64-w64-mingw32", True),
        (("-target", "x86_64-w64-mingw32"), "x86_64-w64-mingw32", True),
        ((), None, False),
    ],
)
def test_only_the_probe_compilers_own_target_keeps_its_include_dirs(
    tokens: tuple[str, ...], own: str | None, suppressed: bool
) -> None:
    assert sysinc._pass_through_suppresses_probe(None, tokens, own) is suppressed


# ── end to end: a Windows-default clang over a MinGW-ABI DLL ──────────────


def _windows_default_clang(tmp: Path) -> Path | None:
    """A ``clang``/``clang++`` pair on a fresh PATH dir that behaves like a
    Windows clang: default target ``x86_64-pc-windows-msvc``, overridable by a
    later ``--target`` exactly as clang's last-wins rule allows."""
    real = shutil.which("clang++")
    if real is None or sys.platform == "win32":
        return None
    bindir = tmp / "winclang"
    bindir.mkdir()
    for name in ("clang", "clang++"):
        wrapper = bindir / name
        target = shutil.which(name) or real
        wrapper.write_text(
            f'#!/bin/sh\nexec "{target}" --target=x86_64-pc-windows-msvc "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    return bindir


def _build_mingw_dll(side: Path, header: str, impl: str) -> None:
    side.mkdir(parents=True)
    (side / "widget.h").write_text("#pragma once\n" + header, encoding="utf-8")
    src = side / "widget.cpp"
    src.write_text(
        '#include "widget.h"\n' + impl + 'extern "C" int DllMainCRTStartup'
        "(void *a, unsigned b, void *c) { return 1; }\n",
        encoding="utf-8",
    )
    cmd = [
        "/usr/bin/env",
        "-i",
        f"PATH={os.environ.get('PATH', '')}",
        shutil.which("clang++") or "clang++",
        "--target=x86_64-w64-windows-gnu",
        "-shared",
        "-nostdlib",
        "-fuse-ld=lld",
        "-Wl,--export-all-symbols",
        f"-I{side}",
        "-o",
        str(side / "libwidget.dll"),
        str(src),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, (cmd, proc.stderr)


@pytest.mark.integration
def test_clang_frontend_reports_a_mingw_dlls_struct_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not (shutil.which("clang++") and shutil.which("ld.lld")):
        pytest.skip("needs clang++ and ld.lld")
    bindir = _windows_default_clang(tmp_path)
    if bindir is None:
        pytest.skip("emulates a Windows clang on a POSIX host")
    keep = ("int keep(int x);\n", "int keep(int x) { return x + 1; }\n")
    old, new = tmp_path / "v1", tmp_path / "v2"
    _build_mingw_dll(old, keep[0] + "struct S_add { int a; };\n", keep[1])
    _build_mingw_dll(new, keep[0] + "struct S_add { int a; int fld_add; };\n", keep[1])
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("ABICHECK_AST_FRONTEND", "clang")
    abi_target._default_triple.cache_clear()  # the wrapper is a new clang

    from abicheck.cli import main

    report = tmp_path / "report.json"
    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(old / "libwidget.dll"),
            str(new / "libwidget.dll"),
            "-H",
            f"old={old / 'widget.h'}",
            "-H",
            f"new={new / 'widget.h'}",
            # Judge every declaration, as the one-change matrix does: the
            # struct is reachable from no export, so the default contract
            # would scope it out on every target.
            "--contract",
            "all",
            "-o",
            f"json={report}",
        ],
        catch_exceptions=False,
    )
    assert report.exists(), result.output
    kinds = {c["kind"] for c in json.loads(report.read_text())["changes"]}
    assert "type_field_added_compatible" in kinds, (sorted(kinds), result.output)
