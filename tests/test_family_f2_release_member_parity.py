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

"""Route-parity cell for defect family F2 on asymmetric header sets.

Companion to harness H2 (``test_family_f2_route_parity.py``), which holds the
settings-by-route matrix. This file pins one invariant the matrix's
single-member release route cannot express: with a header present on one side
only, each member of a two-member release reaches the verdict its own scalar
pair reaches with the same ``-H`` union (``evidence.per_side_operand_divergence``).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_A_H = "#pragma once\nint a_fn(int v);\n"
_B_H = "#pragma once\nint b_fn(int v);\n"


def _cli_compare(args: list[str], out: Path) -> tuple[int, dict]:
    env = {**os.environ, "ABICHECK_AST_FRONTEND": "castxml"}
    proc = subprocess.run(
        [sys.executable, "-m", "abicheck", "compare", *args, "-o", f"json={out}"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert out.exists(), proc.stderr
    return proc.returncode, json.loads(out.read_text())


@pytest.mark.parametrize("side", ["old", "new"])
@pytest.mark.skipif(
    shutil.which("gcc") is None or shutil.which("castxml") is None,
    reason="needs gcc and castxml",
)
def test_release_member_agrees_with_its_scalar_pair_across_asymmetric_headers(
    tmp_path: Path, side: str
) -> None:
    """Route parity, scalar vs release member: with a header present on one
    side only, each member of a two-member release must reach the verdict its
    own scalar pair reaches with the same ``-H`` union. The oracle is the
    scalar compare, which applies no per-member narrowing at all."""
    headers = {
        "old": {"a.h": _A_H, "b.h": _B_H},
        "new": {"a.h": _A_H, "b.h": _B_H},
    }
    headers[side]["notes.h"] = "#pragma once\n/* a_fn is documented here */\n"
    sources = {
        "liba": '#include "a.h"\nint a_fn(int v){return v;}\n',
        "libb": '#include "b.h"\nint b_fn(int v){return v+1;}\n',
    }
    for side_name, files in headers.items():
        inc = tmp_path / side_name / "include"
        lib = tmp_path / side_name / "lib"
        inc.mkdir(parents=True)
        lib.mkdir(parents=True)
        for name, text in files.items():
            (inc / name).write_text(text)
        for member, body in sources.items():
            src = tmp_path / side_name / f"{member}.c"
            src.write_text(body)
            subprocess.run(
                [
                    "gcc",
                    "-shared",
                    "-fPIC",
                    "-g",
                    f"-I{inc}",
                    str(src),
                    "-o",
                    str(lib / f"{member}.so"),
                ],
                check=True,
            )

    headers_args = [
        "--header",
        f"old={tmp_path / 'old/include'}",
        "--header",
        f"new={tmp_path / 'new/include'}",
    ]
    release_code, release = _cli_compare(
        [str(tmp_path / "old/lib"), str(tmp_path / "new/lib"), *headers_args],
        tmp_path / "release.json",
    )
    members = {lib["library"]: lib for lib in release["libraries"]}
    assert set(members) == {"liba.so", "libb.so"}
    for name in ("liba", "libb"):
        scalar_code, scalar = _cli_compare(
            [
                str(tmp_path / f"old/lib/{name}.so"),
                str(tmp_path / f"new/lib/{name}.so"),
                *headers_args,
            ],
            tmp_path / f"{name}.json",
        )
        member = members[f"{name}.so"]
        assert member["verdict"] == scalar["verdict"], (name, member, scalar)
        assert member["verdict"] != "NOT_COMPARABLE", member
    assert release_code != 16
