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

"""A symbols-only re-resolve of an unchanged binary is extracted once.

Bug class ``perf.optimization_wired_to_one_of_several_equivalent_paths``.
``l0_export_delta.fold_l0_hard_removals`` needs a symbols-only view of each
compared side. For ELF it builds that view from the table the snapshot
already carries (``symbols_only_view``); for PE and Mach-O it re-resolves the
binary with ``symbols_only=True`` -- a shape the whole-snapshot cache refused,
so every compare re-extracted both sides from disk a second time. On the
Windows integration lane that doubled-plus the cost of every live compare
and pushed the job past its 40-minute limit.

The invariant, stated over every binary format, with and without headers,
and over every order of full and symbols-only requests: each distinct
request shape is extracted exactly once, and every request is answered with
the snapshot *its own* shape produced (a symbols-only entry never serves a
full dump, nor the reverse). The oracle is the set of distinct shapes in the
sequence, counted independently of the cache.
"""

from __future__ import annotations

import itertools
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from abicheck.model import AbiSnapshot
from abicheck.workflows.dump.cache.api import cached_run_dump

_FORMATS = ("elf", "pe", "macho")
_MAGIC = {"elf": b"\x7fELF", "pe": b"MZ", "macho": b"\xcf\xfa\xed\xfe"}
#: Every length-3 order of full (False) and symbols-only (True) requests.
_SEQUENCES = list(itertools.product((False, True), repeat=3))


def _fake_dump(calls: list[bool]) -> Any:
    def run_dump(*_a: Any, symbols_only: bool = False, **_k: Any) -> AbiSnapshot:
        calls.append(symbols_only)
        # The produced snapshot names the shape that produced it, so a hit
        # served from the other shape's entry is visible in the result.
        return AbiSnapshot(
            library="lib", version="symbols-only" if symbols_only else "full"
        )

    return run_dump


@pytest.mark.parametrize("with_headers", [False, True], ids=["no-headers", "headers"])
@pytest.mark.parametrize("fmt", _FORMATS)
@pytest.mark.parametrize(
    "sequence", _SEQUENCES, ids=lambda s: "".join("SF"[not x] for x in s)
)
def test_each_request_shape_is_extracted_once(
    fmt: str, with_headers: bool, sequence: tuple[bool, ...], tmp_path: Path
) -> None:
    binary = tmp_path / "lib.bin"
    binary.write_bytes(_MAGIC[fmt] + b" payload")
    header = tmp_path / "api.h"
    header.write_text("int api(void);\n", encoding="utf-8")
    headers = [header] if with_headers else []
    calls: list[bool] = []
    for symbols_only in sequence:
        snap = cached_run_dump(
            _fake_dump(calls),
            binary,
            fmt,
            headers,
            [],
            symbols_only=symbols_only,
            header_backend="castxml",
        )
        assert snap.version == ("symbols-only" if symbols_only else "full")
    assert sorted(calls) == sorted(set(sequence))


@pytest.mark.parametrize("fmt", _FORMATS)
def test_a_changed_binary_is_re_extracted_symbols_only(
    fmt: str, tmp_path: Path
) -> None:
    """The entry is content-keyed: new bytes at the same path miss."""
    binary = tmp_path / "lib.bin"
    binary.write_bytes(_MAGIC[fmt] + b" one")
    calls: list[bool] = []
    for payload in (b" one", b" one", b" two", b" two"):
        binary.write_bytes(_MAGIC[fmt] + payload)
        cached_run_dump(_fake_dump(calls), binary, fmt, [], [], symbols_only=True)
    assert calls == [True, True]


# ── public workflow: two `compare` runs of one PE pair ─────────────────────


def _build_pe(out: Path, source: Path) -> bool:
    """Build *source* into a PE DLL at *out*: MinGW gcc on Windows, clang +
    lld targeting windows-gnu elsewhere. ``False`` only when neither
    toolchain is present; a toolchain that runs and fails is a failure."""
    if sys.platform == "win32" and shutil.which("gcc"):
        cmd = ["gcc", "-shared", "-o", str(out), str(source)]
    elif shutil.which("clang") and shutil.which("ld.lld"):
        entry = out.with_suffix(".entry.c")
        entry.write_text(
            "int DllMainCRTStartup(void *a, unsigned b, void *c) { return 1; }\n",
            encoding="utf-8",
        )
        cmd = [
            "clang",
            "--target=x86_64-windows-gnu",
            "-shared",
            "-nostdlib",
            "-fuse-ld=lld",
            "-Wl,--export-all-symbols",
            "-o",
            str(out),
            str(source),
            str(entry),
        ]
    else:
        return False
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, (cmd, proc.stderr)
    return True


@pytest.mark.integration
def test_repeat_pe_compare_extracts_nothing_new(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The user-facing shape: a second identical ``compare`` of a PE pair is
    served wholly from the cache. Before the fix its L0 export probe
    re-extracted both DLLs symbols-only on every run."""
    import abicheck.workflows.dump.pe as pe_mod
    from abicheck.cli import main

    sides = {}
    for side, body in (
        ("old", "int foo(void){return 1;}\nint bar(int x){return x;}\n"),
        ("new", "int foo(void){return 1;}\n"),
    ):
        d = tmp_path / side
        d.mkdir()
        (d / "foo.h").write_text("int foo(void);\nint bar(int);\n", encoding="utf-8")
        (d / "foo.c").write_text(body, encoding="utf-8")
        dll = d / "foo.dll"
        if not _build_pe(dll, d / "foo.c"):
            pytest.skip("no PE toolchain (MinGW gcc, or clang + ld.lld)")
        sides[side] = (dll, d / "foo.h")

    extracted: list[str] = []
    real = pe_mod.extract_pe

    def counting(path: Path, *a: Any, **k: Any) -> AbiSnapshot:
        extracted.append(Path(path).parent.name)
        return real(path, *a, **k)

    monkeypatch.setattr(pe_mod, "extract_pe", counting)
    args = [
        "compare",
        str(sides["old"][0]),
        str(sides["new"][0]),
        "-H",
        f"old={sides['old'][1]}",
        "-H",
        f"new={sides['new'][1]}",
        "-o",
        "json=-",
    ]
    first = CliRunner().invoke(main, args, catch_exceptions=False)
    after_first = len(extracted)
    second = CliRunner().invoke(main, args, catch_exceptions=False)
    assert first.exit_code == second.exit_code, (first.output, second.output)
    assert after_first > 0, "the first compare must extract each side"
    assert extracted[after_first:] == [], extracted
