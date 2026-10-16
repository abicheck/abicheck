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

"""Removing public headers is compared and reported, end to end.

oneDAL 2026.0 deleted 122 public headers (whole ``algorithms/<algo>/``
directories). ``compare old.so new.so -H old=<dir> -H new=<dir>`` exited 16
("not comparable", "manifest/CLI-flag drift") instead of reporting the
removed API. These tests drive the real CLI over real compiled libraries and
real header directories, and check the rendered JSON report: the run
completes, and every function whose header was removed is reported removed.

The unit-level matrix (every set change, drift still refused) lives in
``tests/test_comparability_gate_header_removal.py``; this file proves the
user-facing result through the public workflow.
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
        sys.platform == "win32", reason="builds an ELF .so pair, linux/macos only"
    ),
    pytest.mark.skipif(
        shutil.which("g++") is None, reason="g++ required to build the fixtures"
    ),
]

_KEPT = ("core/core.h", "core/types.h", "algos/kmeans/kmeans.h")
_REMOVED = ("algos/apriori/apriori.h", "algos/adaboost/adaboost.h")

#: shape -> (old headers, new headers)
_SHAPES = {
    "pure-removal": ((*_KEPT, *_REMOVED), _KEPT),
    "removal+addition": ((*_KEPT, *_REMOVED), (*_KEPT, "algos/svm/svm.h")),
    "one-file-removed": (_KEPT, _KEPT[:2]),
}


def _fn(name: str) -> str:
    return "dal_" + Path(name).stem


def _build(root: Path, names) -> Path:
    """A real shared library exporting one function per declared header."""
    include = root / "dal"
    for name in names:
        h = include / name
        h.parent.mkdir(parents=True, exist_ok=True)
        h.write_text(f"int {_fn(name)}(int);\n", encoding="utf-8")
    cpp = root / "lib.cpp"
    cpp.write_text(
        "\n".join(
            [
                *(f'#include "dal/{n}"' for n in names),
                *(f"int {_fn(n)}(int x) {{ return x; }}" for n in names),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    so = root / "libdal.so.1"
    subprocess.run(
        [
            "g++",
            "-std=gnu++11",
            "-fPIC",
            "-shared",
            "-I",
            str(root),
            "-o",
            str(so),
            str(cpp),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return so


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_compare_reports_removed_headers_api_instead_of_refusing(
    tmp_path: Path, shape: str
) -> None:
    old_names, new_names = _SHAPES[shape]
    old_so = _build(tmp_path / "old", old_names)
    new_so = _build(tmp_path / "new", new_names)
    out = tmp_path / "report.json"

    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(old_so),
            str(new_so),
            "--header",
            f"old={tmp_path / 'old' / 'dal'}",
            "--header",
            f"new={tmp_path / 'new' / 'dal'}",
            "-o",
            f"json={out}",
        ],
    )

    assert result.exit_code != 16, result.output
    assert "not comparable" not in result.output.lower()
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["verdict"] == "BREAKING", report["verdict"]
    removed_symbols = " ".join(
        str(c.get("symbol", ""))
        for c in report.get("changes", [])
        if c.get("kind") == "func_removed"
    )
    for name in set(old_names) - set(new_names):
        assert _fn(name) in removed_symbols, (name, removed_symbols)
    # Nothing that survived is reported removed.
    for name in set(old_names) & set(new_names):
        assert _fn(name) not in removed_symbols, name
