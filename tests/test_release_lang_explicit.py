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

"""Evidence-entity-model gap B1: a release member honours a stated language.

``compile.lang: c++`` reached a single-pair ``compare`` and the release
public surface as *stated* (``lang_explicit``), but the per-member fan-out
passed only ``lang`` to ``service.run_compare``, so every member
auto-detected. For a plain-C-looking header in a C++ library that is a
different parse -- an empty struct is 0 bytes in C and 1 in C++ -- so a
one-member release reported different findings from the scalar comparison
of the same two libraries ("One model, any cardinality").
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from abicheck import cli_compare_release_pairwise as pairwise
from abicheck.cli import main


@pytest.mark.parametrize("explicit", [False, True])
def test_member_compare_forwards_lang_explicit(
    monkeypatch: pytest.MonkeyPatch, explicit: bool
) -> None:
    seen: dict[str, Any] = {}

    def _fake(*_a: Any, **kw: Any) -> Any:
        seen.update(kw)
        raise RuntimeError("stop after capturing the request")

    monkeypatch.setattr("abicheck.service.run_compare", _fake)
    with pytest.raises(RuntimeError):
        pairwise._run_compare_pair(
            Path("old.so"), Path("new.so"), [], [], [], [], "1", "2", "c++",
            None, "strict_abi", None, None, None, lang_explicit=explicit,
        )  # fmt: skip
    assert seen["lang"] == "c++"
    assert seen["lang_explicit"] is explicit


@pytest.mark.parametrize("explicit", [False, True])
def test_run_compare_carries_lang_explicit_onto_the_request(
    monkeypatch: pytest.MonkeyPatch, explicit: bool
) -> None:
    from abicheck import service_compare_pipeline as scp

    seen: dict[str, Any] = {}

    def _capture(request: Any, *a: Any, **k: Any) -> Any:
        seen["request"] = request
        raise RuntimeError("stop")

    monkeypatch.setattr(scp, "run_compare_request", _capture)
    with pytest.raises(RuntimeError):
        scp.run_compare(Path("a"), Path("b"), lang="c++", lang_explicit=explicit)
    assert seen["request"].lang_explicit is explicit


def _lib(root: Path, version: str, body: str) -> None:
    inc, lib = root / version / "include", root / version / "lib"
    inc.mkdir(parents=True)
    lib.mkdir(parents=True)
    (inc / "api.h").write_text(
        f"struct Empty {{}};\nstruct S {{ {body} }};\nint use(struct S *s);\n"
    )
    src = root / version / "x.cpp"
    src.write_text(
        'extern "C" {\n#include "api.h"\n}\nint use(struct S *s){return s->x;}\n'
    )
    subprocess.run(
        [
            "g++",
            "-g",
            "-shared",
            "-fPIC",
            f"-I{inc}",
            str(src),
            "-o",
            str(lib / "libx.so"),
        ],
        check=True,
    )


def _kinds(changes: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return sorted((c["kind"], c.get("symbol", "")) for c in changes)


@pytest.mark.integration
def test_one_member_release_matches_the_scalar_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if shutil.which("g++") is None or shutil.which("castxml") is None:
        pytest.skip("needs g++ and castxml")
    _lib(tmp_path, "old", "struct Empty e; int x;")
    _lib(tmp_path, "new", "int x; struct Empty e;")
    (tmp_path / ".abicheck.yml").write_text("compile:\n  lang: c++\n")
    monkeypatch.chdir(tmp_path)
    hdrs = [
        "--header", "old=old/include/api.h", "--header", "new=new/include/api.h",
    ]  # fmt: skip
    runner = CliRunner()
    runner.invoke(
        main,
        ["compare", "old/lib/libx.so", "new/lib/libx.so", *hdrs, "-o", "json=s.json"],
    )
    runner.invoke(main, ["compare", "old/lib", "new/lib", *hdrs, "-o", "json=r.json"])
    scalar = json.loads(Path("s.json").read_text())
    member = json.loads(Path("r.json").read_text())["libraries"][0]
    assert _kinds(member["findings"]) == _kinds(scalar["changes"])
    assert member["verdict"] == scalar["verdict"]
